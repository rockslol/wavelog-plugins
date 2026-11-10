"""Valorant for Wavelog: your rank, RR, matches and rounds, the store with a wishlist, a Tab panel in matches and
quieter Spotify while you play.

Everything comes from the Riot Client on this PC (its lockfile gives a local login), the same unofficial endpoints
other desktop trackers use, and only for your own account. It never touches the game, but a patch can break it.
Names and pictures of agents, maps, ranks and skins come from valorant-api.com.
"""
import asyncio
import base64
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

import httpx
import psutil

from wavelog import Plugin, ui

p = Plugin()
LOCAL = Path(os.environ["LOCALAPPDATA"])
LOCKFILE = LOCAL / "Riot Games" / "Riot Client" / "Config" / "lockfile"
GAMELOG = LOCAL / "VALORANT" / "Saved" / "Logs" / "ShooterGame.log"
EXE = "VALORANT-Win64-Shipping.exe"
PLATFORM = base64.b64encode(json.dumps({"platformType": "PC", "platformOS": "Windows", "platformOSVersion": "10.0.19042.1.256.64bit",
                                        "platformChipset": "Unknown"}).encode()).decode()
QUEUES = {"competitive": "Competitive", "unrated": "Unrated", "swiftplay": "Swiftplay", "spikerush": "Spike Rush",
          "deathmatch": "Deathmatch", "ggteam": "Escalation", "hurm": "Team Deathmatch", "premier": "Premier",
          "skirmish2v2": "Skirmish", "abilitydraftarena": "Ability Draft", "": "Custom"}
VP = "85ad13f7-3d1b-5128-9eb2-7cd8ee0b5741"
SCHEMA = """CREATE TABLE IF NOT EXISTS val_matches (id TEXT PRIMARY KEY, puuid TEXT, started_at INTEGER, length INTEGER, map TEXT,
    map_id TEXT, mode TEXT, agent TEXT, agent_id TEXT, won INTEGER, rounds_won INTEGER, rounds_lost INTEGER, kills INTEGER,
    deaths INTEGER, assists INTEGER, score INTEGER, headshots INTEGER, shots INTEGER, damage INTEGER, rounds INTEGER,
    rr INTEGER, tier INTEGER, round_data TEXT)"""
GREEN, RED, GREY = "#30d158", "#ff453a", "#8e8e93"
HOW = {"Eliminated": "elimination", "Bomb detonated": "spike", "Bomb defused": "defuse", "Round timer expired": "time"}

local = httpx.AsyncClient(verify=False, timeout=10)  # the Riot Client's own https server on 127.0.0.1
sync_lock, shop_lock = asyncio.Lock(), asyncio.Lock()
mem = {"at": 0, "error": None, "assets": None, "assets_at": 0, "skins": None, "skins_at": 0, "running": False, "running_at": 0}
live = {}  # the match you're in: phase, match id, agent, cached for 10 s
tasks = set()


# --- Saved state -------------------------------------------------------------------------------------------------
def db(ctx):
    if not mem.get("db"):
        ctx.db.execute(SCHEMA)
        mem["db"] = True
    return ctx.db


def saved(ctx, name):
    """account.json (puuid, Riot ID, rank) and shop.json (today's store), kept in memory once read."""
    if name not in mem:
        f = ctx.data / f"{name}.json"
        mem[name] = json.loads(f.read_text("utf-8")) if f.exists() else {}
    return mem[name]


def keep(ctx, name):
    (ctx.data / f"{name}.json").write_text(json.dumps(mem[name]), "utf-8")


def in_valorant():
    if time.time() - mem["running_at"] > 10:
        mem["running_at"] = time.time()
        mem["running"] = any((x.info["name"] or "").lower().startswith("valorant") for x in psutil.process_iter(["name"]))
    return mem["running"]


# --- Riot ----------------------------------------------------------------------------------------------------------
async def assets(ctx):
    if mem["assets"] and time.time() - mem["assets_at"] < 86400:
        return mem["assets"]
    get = lambda path, **q: ctx.http.get("https://valorant-api.com/v1/" + path, params=q)
    tiers, agents, maps, version = await asyncio.gather(get("competitivetiers"), get("agents", isPlayableCharacter="true"), get("maps"), get("version"))
    mem["assets"] = {"tiers": {t["tier"]: {"name": t["tierName"].title(), "icon": t["largeIcon"]} for t in tiers.json()["data"][-1]["tiers"]},
                     "agents": {a["uuid"].lower(): {"name": a["displayName"], "icon": a["displayIcon"]} for a in agents.json()["data"]},
                     "maps": {m["mapUrl"]: {"name": m["displayName"], "icon": m["listViewIcon"]} for m in maps.json()["data"]},
                     "version": version.json()["data"]["riotClientVersion"]}
    mem["assets_at"] = time.time()
    return mem["assets"]


async def safe_assets(ctx):
    try:
        return await assets(ctx)
    except Exception:
        return mem["assets"] or {"tiers": {}, "agents": {}, "maps": {}}


async def login(ctx):
    _, _, port, password, _ = LOCKFILE.read_text().split(":")
    auth = {"Authorization": "Basic " + base64.b64encode(f"riot:{password}".encode()).decode()}
    t = (await local.get(f"https://127.0.0.1:{port}/entitlements/v1/token", headers=auth)).json()
    me = (await local.get(f"https://127.0.0.1:{port}/chat/v1/session", headers=auth)).json()
    acc = saved(ctx, "account")
    if me.get("game_name"):
        acc["riot_id"] = f"{me['game_name']}#{me['game_tag']}"
    log_text = GAMELOG.read_text(errors="ignore") if GAMELOG.exists() else ""
    shard = next(iter(re.findall(r"https://pd\.(\w+)\.a\.pvp\.net", log_text)), None)
    version = next(iter(re.findall(r"CI server version: (\S+)", log_text)), None) or (await assets(ctx))["version"]
    if not shard:
        region = (await local.get(f"https://127.0.0.1:{port}/riotclient/region-locale", headers=auth)).json()["region"].lower()
        shard = {"latam": "na", "br": "na"}.get(region, region)
    glz = next(iter(re.findall(r"https://(glz-[\w-]+\.\w+\.a\.pvp\.net)", log_text)), f"glz-{shard}-1.{shard}.a.pvp.net")
    return t["subject"], shard, {"Authorization": "Bearer " + t["accessToken"], "X-Riot-Entitlements-JWT": t["token"],
                                 "X-Riot-ClientPlatform": PLATFORM, "X-Riot-ClientVersion": version}, (port, auth, glz)


def save_match(ctx, m, puuid, a):
    info = m["matchInfo"]
    me = next((x for x in m["players"] if x["subject"] == puuid), None)
    if not me or not me.get("stats"):
        return
    teams = m.get("teams") or []
    team = next((t for t in teams if t["teamId"] == me["teamId"]), None)
    other = next((t for t in teams if t["teamId"] != me["teamId"]), None) if len(teams) == 2 else None
    won = None if not team or (other and team["roundsWon"] == other["roundsWon"]) else int(team["won"])
    hits = [d for r in m.get("roundResults") or [] for x in r.get("playerStats") or [] if x["subject"] == puuid for d in x.get("damage") or []]
    s, agent = me["stats"], a["agents"].get(me["characterId"].lower(), {})
    rounds = []
    for r in m.get("roundResults") or []:
        ps = r.get("playerStats") or []
        kills = sorted((k for x in ps for k in x.get("kills") or []), key=lambda k: k.get("timeSinceRoundStartMillis", 0))
        mine = next((x for x in ps if x["subject"] == puuid), {})
        rounds.append({"won": r.get("winningTeam") == me["teamId"] if r.get("winningTeam") else None, "how": r.get("roundResult", ""),
                       "k": sum(k["killer"] == puuid for k in kills), "dmg": sum(d["damage"] for d in mine.get("damage") or []),
                       "died": any(k["victim"] == puuid for k in kills), "fb": bool(kills) and kills[0]["killer"] == puuid,
                       "fd": bool(kills) and kills[0]["victim"] == puuid, "plant": r.get("bombPlanter") == puuid,
                       "defuse": r.get("bombDefuser") == puuid, "loadout": (mine.get("economy") or {}).get("loadoutValue", 0)})
    db(ctx).execute(
        "INSERT OR REPLACE INTO val_matches (id, puuid, started_at, length, map, map_id, mode, agent, agent_id, won, rounds_won, "
        "rounds_lost, kills, deaths, assists, score, headshots, shots, damage, rounds, round_data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (info["matchId"], puuid, info["gameStartMillis"], info.get("gameLengthMillis") or 0,
         a["maps"].get(info["mapId"], {}).get("name", info["mapId"].split("/")[-1]), info["mapId"],
         QUEUES.get(info.get("queueID") or "", (info.get("queueID") or "").title()), agent.get("name", "?"), me["characterId"].lower(),
         won, team and team["roundsWon"], other and other["roundsWon"], s["kills"], s["deaths"], s["assists"], s["score"],
         sum(d["headshots"] for d in hits), sum(d["headshots"] + d["bodyshots"] + d["legshots"] for d in hits),
         sum(d["damage"] for d in hits), s.get("roundsPlayed") or 1, json.dumps(rounds, separators=(",", ":"))))


async def sync(ctx, force=False):
    """Match history (last 20), RR per ranked game and your rank, from Riot. At most every 90 s unless forced."""
    if sync_lock.locked() or (not force and time.time() - mem["at"] < 90):
        return
    async with sync_lock:
        mem["at"] = time.time()
        if not LOCKFILE.exists():
            mem["error"] = "Open the Riot Client to update"
            return
        try:
            puuid, shard, hdr, _ = await login(ctx)
            pd, a = f"https://pd.{shard}.a.pvp.net", await assets(ctx)
            get = lambda path, **q: ctx.http.get(pd + path, params=q, headers=hdr)
            history = (await get(f"/match-history/v1/history/{puuid}", endIndex=20)).json()["History"]
            known = {r[0] for r in db(ctx).execute("SELECT id FROM val_matches WHERE puuid = ? AND round_data IS NOT NULL", (puuid,))}
            for h in history:
                if h["MatchID"] not in known:
                    r = await get(f"/match-details/v1/matches/{h['MatchID']}")
                    if r.status_code == 200:
                        save_match(ctx, r.json(), puuid, a)
                    await asyncio.sleep(1)
            for u in (await get(f"/mmr/v1/players/{puuid}/competitiveupdates", endIndex=20, queue="competitive")).json()["Matches"]:
                db(ctx).execute("UPDATE val_matches SET rr = ?, tier = ? WHERE id = ?", (u["RankedRatingEarned"], u["TierAfterUpdate"], u["MatchID"]))
            mmr = (await get(f"/mmr/v1/players/{puuid}")).json()
            last = mmr.get("LatestCompetitiveUpdate") or {}
            seasons = ((mmr.get("QueueSkills") or {}).get("competitive") or {}).get("SeasonalInfoBySeasonID") or {}
            peak = max((int(t) for s in seasons.values() for t in (s.get("WinsByTier") or {})), default=0)
            saved(ctx, "account").update(puuid=puuid, peak=peak, current={"tier": last.get("TierAfterUpdate", 0), "rr": last.get("RankedRatingAfterUpdate", 0),
                                                                        "last_change": last.get("RankedRatingEarned", 0)})
            keep(ctx, "account")
            mem["error"] = None
        except Exception as e:
            ctx.log.warning("Valorant sync failed: %r", e)
            mem["error"] = "Couldn't reach Riot, will retry"


async def live_match(ctx):
    """Which phase you're in (menu, agent select, in a match) and, in a match, the map, mode and your agent."""
    if not LOCKFILE.exists():
        return None
    if time.time() - live.get("at", 0) < 10:
        return live.get("data")
    live["at"] = time.time()
    puuid, shard, hdr, (port, auth, glz) = await login(ctx)
    pres = (await local.get(f"https://127.0.0.1:{port}/chat/v4/presences", headers=auth)).json()["presences"]
    me = next((x for x in pres if x["puuid"] == puuid and x.get("product") == "valorant" and x.get("private")), None)
    m = (json.loads(base64.b64decode(me["private"])).get("matchPresenceData") or {}) if me else {}
    data, phase, was = None, m.get("sessionLoopState"), live.get("phase")
    live["phase"] = phase
    if was == "INGAME" and phase != "INGAME" and live.get("match"):
        t = asyncio.create_task(catch_up(ctx, live["match"]))
        tasks.add(t)
        t.add_done_callback(tasks.discard)
    if phase in ("INGAME", "PREGAME"):
        a = await safe_assets(ctx)
        data = {"state": phase, "map_id": m.get("matchMap", ""), "mode": QUEUES.get(m.get("queueId") or "", (m.get("queueId") or "").title()),
                "map": a["maps"].get(m.get("matchMap", ""), {}).get("name", ""), "agent_id": None}
        if phase == "INGAME":
            r = await ctx.http.get(f"https://{glz}/core-game/v1/players/{puuid}", headers=hdr)
            if r.status_code == 200:
                mid = r.json()["MatchID"]
                if live.get("match") != mid:
                    players = (await ctx.http.get(f"https://{glz}/core-game/v1/matches/{mid}", headers=hdr)).json()["Players"]
                    live.update(match=mid, agent=next((x["CharacterID"].lower() for x in players if x["Subject"] == puuid), None))
                data["agent_id"] = live.get("agent")
    live["data"] = data
    return data


async def catch_up(ctx, mid):
    """A match just ended. Riot lists it a little later, so look a few times until it's there, then tell the notch."""
    for wait in (10, 20, 30, 60, 120, 180):
        await asyncio.sleep(wait)
        await sync(ctx, force=True)
        row = db(ctx).execute("SELECT * FROM val_matches WHERE id = ?", (mid,)).fetchone()
        if row:
            ctx.log.info("Valorant match %s saved", mid)
            if ctx.settings["match_alert"]:
                m, a = dict(row), await safe_assets(ctx)
                rr = m["mode"] == "Competitive" and m["rr"] is not None
                ctx.alert(f"{RES[result(m)]} {score(m)} · {m['map']}", f"{signed(m['rr'])} RR" if rr else kda(m),
                          icon=a["tiers"].get(m["tier"], {}).get("icon") or a["agents"].get(m["agent_id"], {}).get("icon"),
                          color=GREY if m["won"] is None else GREEN if m["won"] else RED, short=signed(m["rr"]) if rr else RES[result(m)])
            return


# --- Store, night market, wishlist ---------------------------------------------------------------------------------
async def skins(ctx):
    """Skin level uuid -> skin (uuid, name, picture, rarity colour). Cached for a day."""
    if mem["skins"] and time.time() - mem["skins_at"] < 86400:
        return mem["skins"]
    get = lambda path: ctx.http.get("https://valorant-api.com/v1/" + path, timeout=60)
    r_skins, r_tiers = await asyncio.gather(get("weapons/skins"), get("contenttiers"))
    tiers = {t["uuid"]: {"name": t["devName"], "color": "#" + (t.get("highlightColor") or "ffffff80")[:6]} for t in r_tiers.json()["data"]}
    levels, by_skin = {}, {}
    for s in r_skins.json()["data"]:
        if "Random" in s["displayName"] or s["displayName"].startswith("Standard"):
            continue
        lv = s.get("levels") or [{}]
        info = {"id": s["uuid"], "name": s["displayName"], "icon": lv[0].get("displayIcon") or s.get("displayIcon"),
                "tier": tiers.get(s.get("contentTierUuid") or "", {})}
        by_skin[s["uuid"]] = info
        for level in lv:
            if level.get("uuid"):
                levels[level["uuid"].lower()] = info
    mem.update(skins=levels, skins_by_id=by_skin, skins_at=time.time())
    return levels


async def fetch_shop(ctx):
    puuid, shard, hdr, _ = await login(ctx)
    r = await ctx.http.post(f"https://pd.{shard}.a.pvp.net/store/v3/storefront/{puuid}", headers=hdr, json={})
    r.raise_for_status()
    j, now, sk = r.json(), time.time(), await skins(ctx)

    def item(offer, cost=None):
        rid = offer["Rewards"][0]["ItemID"].lower()
        s = sk.get(rid) or {"id": rid, "name": "Unknown skin", "icon": None, "tier": {}}
        return {**s, "price": (cost or offer.get("Cost") or {}).get(VP)}

    panel, bonus = j.get("SkinsPanelLayout") or {}, j.get("BonusStore")
    shop = saved(ctx, "shop")
    shop.clear()
    shop.update(puuid=puuid, at=now, daily=[item(o) for o in panel.get("SingleItemStoreOffers", [])],
                daily_ends=now + panel.get("SingleItemOffersRemainingDurationInSeconds", 0))
    if bonus:
        shop.update(night=[{**item(b["Offer"], b.get("DiscountCosts")), "was": (b["Offer"].get("Cost") or {}).get(VP),
                            "discount": b.get("DiscountPercent")} for b in bonus.get("BonusStoreOffers", [])],
                    night_ends=now + bonus.get("BonusStoreRemainingDurationInSeconds", 0))
    keep(ctx, "shop")


async def shop_now(ctx, refresh=False):
    """Today's store and the night market. Read from Riot once per shop rotation (00:00 UTC), or when asked."""
    shop, now, error = saved(ctx, "shop"), time.time(), None
    if (refresh or now > shop.get("daily_ends", 0)) and LOCKFILE.exists() and not shop_lock.locked():
        async with shop_lock:
            try:
                await fetch_shop(ctx)
            except Exception as e:
                ctx.log.warning("Store failed: %r", e)
                error = "Couldn't read the store from Riot, will retry"
    fresh = now < shop.get("daily_ends", 0)
    return {"daily": shop.get("daily", []) if fresh else [], "daily_ends": shop.get("daily_ends"),
            "night": shop.get("night", []) if now < (shop.get("night_ends") or 0) else [], "night_ends": shop.get("night_ends"),
            "error": error or (None if fresh else "Open the Riot Client to see today's store")}


def wishlist(ctx):
    return ctx.store.setdefault("wishlist", [])


# --- Numbers -------------------------------------------------------------------------------------------------------
RES = {"W": "Win", "L": "Loss", "D": "Draw"}


def signed(n):
    return f"+{n}" if (n or 0) > 0 else str(n or 0)


def result(m):
    return "D" if m["won"] is None else "W" if m["won"] else "L"


def score(m):
    return f"{m['rounds_won'] or 0} rounds" if m["rounds_lost"] is None else f"{m['rounds_won'] or 0}-{m['rounds_lost']}"


def kda(m):
    return f"{m['kills']}/{m['deaths']}/{m['assists']}"


def pct(a, b):
    return f"{round(100 * a / b)}%" if b else "None"


def when(ms):
    return time.strftime("%b %d, %H:%M", time.localtime(ms / 1000)).replace(" 0", " ")


def left(t):
    s = max(0, int((t or 0) - time.time()))
    return f"{s // 3600} h {s % 3600 // 60} min" if s >= 3600 else f"{s // 60} min"


def totals(rows):
    k, d, shots, rounds = (sum(r[x] or 0 for r in rows) for x in ("kills", "deaths", "shots", "rounds"))
    decided = [r for r in rows if r["won"] is not None]
    return {"games": len(rows), "wins": sum(r["won"] for r in decided), "decided": len(decided),
            "kd": round(k / max(d, 1), 2), "hs": round(100 * sum(r["headshots"] or 0 for r in rows) / max(shots, 1)),
            "adr": round(sum(r["damage"] or 0 for r in rows) / max(rounds, 1)), "acs": round(sum(r["score"] or 0 for r in rows) / max(rounds, 1))}


def grouped(rows, key):
    by = defaultdict(list)
    for r in rows:
        by[r[key]].append(r)
    return sorted(({"name": n, "id": g[0][key + "_id"], **totals(g)} for n, g in by.items()), key=lambda x: -x["games"])


def matches(ctx, puuid):
    rows = [dict(r) for r in db(ctx).execute("SELECT * FROM val_matches WHERE puuid = ? ORDER BY started_at DESC", (puuid,))]
    for r in rows:
        r["round_list"] = json.loads(r.pop("round_data") or "[]")
    return rows


def win_songs(ctx, rows):
    """Songs that played during your matches, best win rate first."""
    by = {}
    for m in rows[:150]:
        if m["won"] is None:
            continue
        heard = {x["id"]: x for x in ctx.plays(m["started_at"] / 1000, (m["started_at"] + m["length"]) / 1000 + 60)}
        for i, s in heard.items():
            e = by.setdefault(i, {**s, "games": 0, "wins": 0})
            e["games"] += 1
            e["wins"] += m["won"]
    return sorted(by.values(), key=lambda x: (-x["wins"] / x["games"], -x["games"]))[:8]


def strip(rounds):
    """The rounds as a timeline: green won, red lost, taller = more kills, faded = you died, dot = first blood."""
    half = 3 if len(rounds) <= 9 else 11
    return [ui.seg("good" if r["won"] else "bad" if r["won"] is not None else None, min(r.get("k") or 0, 5), faded=r.get("died"),
                   dot=r.get("fb"), gap=i in (half, 23),
                   tip=f"Round {i + 1}: {'no team winner' if r['won'] is None else ('won' if r['won'] else 'lost') + ' by ' + HOW.get(r.get('how'), r.get('how') or '?')}"
                       f" · {r.get('k', 0)} kills · {r.get('dmg', 0)} damage{' · first blood' if r.get('fb') else ''}{' · planted' if r.get('plant') else ''}")
            for i, r in enumerate(rounds)]


def match_item(m, a, rounds=True):
    rr = m["rr"] if m["mode"] == "Competitive" else None
    hs = pct(m["headshots"] or 0, m["shots"]) if m["shots"] else "0%"
    return ui.item(f"{m['map']} · {RES[result(m)]} {score(m)}",
                   f"{m['mode']} · {m['agent']} · {kda(m)} · {hs} HS · {round((m['score'] or 0) / max(m['rounds'] or 1, 1))} ACS · {when(m['started_at'])}",
                   image=a["agents"].get(m["agent_id"], {}).get("icon"), right=f"{signed(rr)} RR" if rr is not None else None,
                   mood="up" if (rr or 0) > 0 else "down" if (rr or 0) < 0 else None, strip=strip(m["round_list"]) if rounds else None)


def result_chips(rows, n=5):
    return [ui.chip(result(m), GREY if m["won"] is None else GREEN if m["won"] else RED) for m in rows[:n]]


async def ready(ctx):
    """Your account and matches, or None before the first sync worked."""
    acc = saved(ctx, "account")
    if not acc.get("puuid"):
        if sync_lock.locked():  # the first sync is running: wait for it
            async with sync_lock:
                pass
        else:
            await sync(ctx, force=True)
    return acc if acc.get("puuid") else None


def not_ready():
    return [ui.hero("Your rank, RR and matches", mem["error"] or "Open the Riot Client on this PC and it fills in.",
                    label="Valorant", button="Try again", action="refresh")]


# --- Background jobs -----------------------------------------------------------------------------------------------
@p.job(every=lambda ctx: 120 if in_valorant() else 900, wait=5)
async def history(ctx):
    await sync(ctx)


@p.job(every=15, wait=10)
async def match(ctx):
    """While Valorant runs, follow the match so a finished one shows up (and gets its alert) right away."""
    if in_valorant() and LOCKFILE.exists():
        try:
            await live_match(ctx)
        except Exception as e:  # the Riot Client starting or closing
            ctx.log.debug("Valorant watch: %r", e)


@p.job(every=600, wait=30)
async def store(ctx):
    """Reads the store after it resets, and tells the notch once when a wishlisted skin is in it."""
    shop = await shop_now(ctx)
    wanted = {w["id"] for w in wishlist(ctx)}
    hit = next((s for s in shop["daily"] + shop["night"] if s["id"] in wanted), None)
    key = hit and f"{hit['id']}:{round(shop['daily_ends'] or 0)}"
    if hit and ctx.settings["store_alert"] and ctx.store.get("alerted") != key:
        ctx.store["alerted"] = key
        ctx.save()
        ctx.alert(f"{hit['name']} is in your store", f"{hit['price']:,} VP" if hit.get("price") else "Store", icon=hit["icon"], color="#ff4655", short="Store")


@p.duck
def in_match(ctx):
    return in_valorant() and live.get("phase") == "INGAME"


# --- Stats window --------------------------------------------------------------------------------------------------
@p.page("Valorant", choices=[("all", "All modes"), ("comp", "Competitive")])
async def page(ctx, choice):
    acc = await ready(ctx)
    if not acc:
        return not_ready()
    a, rows = await safe_assets(ctx), matches(ctx, acc["puuid"])
    cur = acc.get("current") or {}
    tier, peak = a["tiers"].get(cur.get("tier", 0)) or {}, a["tiers"].get(acc.get("peak"))
    shown = [m for m in rows if choice == "all" or m["mode"] == "Competitive"]
    s = totals(shown)
    updated = f"Updated {time.strftime('%H:%M', time.localtime(mem['at']))}" if mem["at"] else None
    out = [ui.header(tier.get("name") or "Unranked",
                     " · ".join(filter(None, [acc.get("riot_id"), f"{cur.get('rr', 0)} RR", f"last ranked game {signed(cur.get('last_change'))}",
                                              peak and f"peak this act {peak['name']}"])),
                     image=tier.get("icon"), bar=min(1, (cur.get("rr") or 0) / 100), note=mem["error"] or updated, chips=result_chips(rows)),
           ui.buttons(ui.button("Refresh", "refresh"))]
    out += await shop_blocks(ctx)
    out.append(ui.tiles(ui.tile("Matches", s["games"], f"{s['wins']} won"), ui.tile("Win rate", pct(s["wins"], s["decided"])),
                        ui.tile("K/D", f"{s['kd']:.2f}"), ui.tile("Headshot %", f"{s['hs']}%"),
                        ui.tile("ADR", s["adr"], "Damage per round"), ui.tile("ACS", s["acs"], "Combat score")))
    out.append(ui.list("Matches", [match_item(m, a) for m in shown[:20]], empty="No matches yet.",
                       hint="Each bar is a round · taller = more kills · faded = you died · dot = first blood"))
    out.append(ui.columns(
        ui.list("Agents", [ui.item(x["name"], f"{x['games']} {'match' if x['games'] == 1 else 'matches'} · {pct(x['wins'], x['decided'])} won · {x['kd']:.2f} K/D · {x['acs']} ACS",
                                   a["agents"].get(x["id"], {}).get("icon")) for x in grouped(shown, "agent")[:8]]),
        ui.list("Maps", [ui.item(x["name"], f"{x['games']} {'match' if x['games'] == 1 else 'matches'} · {pct(x['wins'], x['decided'])} won",
                                 a["maps"].get(x["id"], {}).get("icon"), meter=x["wins"] / x["decided"] if x["decided"] else 0) for x in grouped(shown, "map")]),
    ))
    songs = win_songs(ctx, shown)
    out.append(ui.list("Songs you win with", [ui.item(x["name"], f"{x['artists']} · {x['wins']} of {x['games']} won", x["image"],
                                                      right=pct(x["wins"], x["games"])) for x in songs],
                       hint="What played during your matches", empty="Play Valorant with Spotify on and this fills in."))
    return out


async def shop_blocks(ctx):
    shop, wl = await shop_now(ctx), wishlist(ctx)
    wanted = {w["id"] for w in wl}
    card = lambda s, night=False: ui.card(s["name"], f"{s['price']:,} VP" + (f" · was {s['was']:,}" if night and s.get("was") else "") if s.get("price") else None,
                                          s["icon"], color=(s.get("tier") or {}).get("color"), badge=f"-{s['discount']}%" if night and s.get("discount") else None,
                                          on=s["id"] in wanted, action="wish", args={"id": s["id"], "name": s["name"], "icon": s["icon"]})
    out = [ui.grid("Store", [card(s) for s in shop["daily"]], hint=f"Resets in {left(shop['daily_ends'])}" if shop["daily"] else shop["error"],
                   empty="Open the Riot Client to see today's store")]
    if shop["night"]:
        out.append(ui.grid("Night market", [card(s, True) for s in shop["night"]], hint=f"Ends in {left(shop['night_ends'])}", columns=3))
    if wl:
        out.append(ui.chips([ui.chip(w["name"] + " ×", "#ff4655", "wish", w) for w in wl], title="Wishlist"))
    out.append(ui.search("Add to your wishlist" if not wl else "Add another skin", "find_skin", "A skin, e.g. Reaver Vandal",
                         hint="The notch tells you when one is in your store"))
    return out


@p.action
async def refresh(ctx):
    await sync(ctx, force=time.time() - mem["at"] > 30)
    await shop_now(ctx, refresh=True)


@p.action
async def wish(ctx, id, name="", icon=None):
    wl = wishlist(ctx)
    if any(w["id"] == id for w in wl):
        wl[:] = [w for w in wl if w["id"] != id]
    else:
        wl.append({"id": id, "name": name, "icon": icon})
    ctx.save()


@p.action
async def find_skin(ctx, q):
    await skins(ctx)
    q = q.strip().lower()
    wanted = {w["id"] for w in wishlist(ctx)}
    hits = sorted((s for s in mem["skins_by_id"].values() if q in s["name"].lower()), key=lambda s: (not s["name"].lower().startswith(q), s["name"]))[:12]
    return [ui.item(s["name"], image=s["icon"], right="♥" if s["id"] in wanted else None, action="wish",
                    args={"id": s["id"], "name": s["name"], "icon": s["icon"]}) for s in hits]


# --- Widget, notch, Tab panel --------------------------------------------------------------------------------------
@p.widget("Valorant", sizes="sml")
async def rank(ctx, size):
    acc = await ready(ctx)
    if not acc:
        return [ui.text("Open the Riot Client and your rank shows up here.")]
    a, rows = await safe_assets(ctx), matches(ctx, acc["puuid"])
    cur = acc.get("current") or {}
    tier = a["tiers"].get(cur.get("tier", 0)) or {}
    out = [ui.header(tier.get("name") or "Unranked", f"{cur.get('rr', 0)} RR · {signed(cur.get('last_change'))}", image=tier.get("icon"),
                     bar=min(1, (cur.get("rr") or 0) / 100) if size != "s" else None, chips=result_chips(rows) if size != "s" else None)]
    if size == "l":
        out.append(ui.list(None, [match_item(m, a, rounds=False) for m in rows[:4]]))
    return out


@p.notch_section("Last Valorant match")
async def last_match(ctx):
    acc = saved(ctx, "account")
    rows = matches(ctx, acc["puuid"])[:1] if acc.get("puuid") else []
    if not rows:
        return []
    return [ui.list(None, [match_item(rows[0], await safe_assets(ctx))])]


def record(label, rows):
    t = totals(rows)
    return ui.tile(label, f"{t['wins']}-{t['decided'] - t['wins']}" if t["games"] else "First time",
                   f"{pct(t['wins'], t['decided'])} · {t['kd']:.2f} K/D" if t["games"] else None)


@p.overlay(game=EXE, key="Tab", width=740, height=210, y="tab_y")
async def tab_panel(ctx):
    """Hold Tab in a match: your rank, today, this map and agent, and your last match."""
    if not ctx.settings["tab_panel"] or not (acc := saved(ctx, "account")).get("puuid"):
        return []
    try:
        lv = await live_match(ctx)
    except Exception:
        lv = None
    a, rows = await safe_assets(ctx), matches(ctx, acc["puuid"])
    cur = acc.get("current") or {}
    tier = a["tiers"].get(cur.get("tier", 0)) or {}
    midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)) * 1000
    today = [m for m in rows if m["started_at"] >= midnight]
    t = totals(today)
    agent = lv and lv.get("agent_id") and a["agents"].get(lv["agent_id"])
    tiles = [ui.tile(tier.get("name") or "Unranked", f"{cur.get('rr', 0)} RR", f"last game {signed(cur.get('last_change'))}"),
             ui.tile("Today", f"{t['wins']}-{t['decided'] - t['wins']}", f"{signed(sum(m['rr'] or 0 for m in today))} RR · {t['kd']:.2f} K/D" if t["games"] else None)]
    if lv and lv.get("map"):
        tiles.append(record(lv["map"], [m for m in rows if m["map_id"] == lv["map_id"]]))
    if agent:
        tiles.append(record(agent["name"], [m for m in rows if m["agent_id"] == lv["agent_id"]]))
    out = [ui.tiles(tiles)]
    if rows:
        out.append(ui.list(None, [match_item(rows[0], a)]))
    if now := ctx.now():
        out.append(ui.text(f"♪ {now['name']} · {now['artists']}"))
    return out

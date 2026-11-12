"""Today: your listening today. Small on purpose: copy this folder to start your own plugin."""
import time
from collections import Counter

from wavelog import Plugin, ui

p = Plugin()


def midnight():
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


@p.job(every=60)
async def count(ctx):
    """Background job: tells you once when you reach your daily goal."""
    minutes = sum(x["ms"] or 0 for x in ctx.plays(midnight(), time.time())) / 60000
    day = time.strftime("%Y-%m-%d")
    if minutes >= ctx.settings["goal"] and ctx.store.get("goal_day") != day:
        ctx.store["goal_day"] = day
        ctx.save()
        ctx.alert("Daily goal reached", f"{round(minutes)} minutes today")


@p.page("Today")
async def today(ctx):
    plays = ctx.plays(midnight(), time.time())
    hearts = set(ctx.store.get("hearts", []))
    minutes = sum(x["ms"] or 0 for x in plays) / 60000
    artists = Counter(a.strip() for x in plays for a in (x["artists"] or "").split(",") if a.strip())
    songs = Counter(x["id"] for x in plays)
    by_id = {x["id"]: x for x in plays}
    goal = ctx.settings["goal"]
    out = [ui.header(ctx.settings["greeting"] or "Today", f"{round(minutes)} of {goal:g} minutes",
                     image=plays[-1]["image"] if plays else None, bar=min(1, minutes / goal),
                     note="Goal reached" if minutes >= goal else None)]
    if not plays:
        return out + [ui.hero("Nothing yet today", "Play something on Spotify and it shows up here.")]
    out.append(ui.tiles(ui.tile("Plays", len(plays)), ui.tile("Minutes", round(minutes)),
                        ui.tile("Songs", len(songs)), ui.tile("Top artist", artists.most_common(1)[0][0])))
    if ctx.settings["chart"] == "hour":
        hours = Counter(time.localtime(x["ended"]).tm_hour for x in plays)
        out.append(ui.bars("By hour", [(f"{h:02d}", round(sum((x["ms"] or 0) for x in plays if time.localtime(x["ended"]).tm_hour == h) / 60000))
                                       for h in range(time.localtime().tm_hour + 1)], unit="min", hint=f"Busiest {hours.most_common(1)[0][0]:02d}:00"))
    top = songs.most_common(8)
    out.append(ui.columns(
        ui.list("Most played", [ui.item(by_id[i]["name"], by_id[i]["artists"], by_id[i]["image"], right=f"{n}×", meter=n / top[0][1])
                                for i, n in top], ranked=True),
        ui.list("Artists", [ui.item(a, right=str(n), mood="up" if n >= 3 else None) for a, n in artists.most_common(8)]),
    ))
    if ctx.settings["covers"]:
        seen = list(dict.fromkeys(x["id"] for x in reversed(plays)))[:8]
        out.append(ui.grid("Covers", [ui.card(by_id[i]["name"], by_id[i]["artists"], by_id[i]["image"], on=i in hearts, action="heart", args={"id": i},
                                              badge="Top" if i == top[0][0] else None) for i in seen],
                           hint="Click the heart to keep a song"))
    if hearts:
        out.append(ui.chips([ui.chip(by_id[h]["name"] if h in by_id else h, color="#ff4655", action="heart", args={"id": h}) for h in hearts],
                            title="Hearted"))
    out.append(ui.search("Find a song from today", "find", placeholder="Song or artist"))
    out.append(ui.buttons(ui.button("Test alert", "test_alert"), ui.button("Clear hearts", "clear")))
    out.append(ui.text("This page is a plugin. Its code is in the plugin folder, see Settings → Plugins."))
    return out


def summary(ctx):
    plays = ctx.plays(midnight(), time.time())
    minutes = sum(x["ms"] or 0 for x in plays) / 60000
    artists = Counter(a.strip() for x in plays for a in (x["artists"] or "").split(",") if a.strip())
    return plays, minutes, artists


@p.widget("Today", sizes="sml")
async def card(ctx, size):
    """A desktop widget. `size` is "s", "m" or "l"; small widgets get fewer blocks."""
    plays, minutes, artists = summary(ctx)
    goal = ctx.settings["goal"]
    out = [ui.header("Today", f"{round(minutes)} of {goal:g} min", bar=min(1, minutes / goal))]
    if size != "s":
        out.append(ui.tiles(ui.tile("Plays", len(plays)), ui.tile("Top artist", artists.most_common(1)[0][0] if artists else "None")))
    if size == "l" and plays:
        out.append(ui.list("Last played", [ui.item(x["name"], x["artists"], x["image"]) for x in plays[-4:][::-1]]))
    return out


@p.notch_section("Today")
async def notch(ctx):
    """A section in the notch's dashboard: keep it to a line or two."""
    plays, minutes, artists = summary(ctx)
    return [ui.tiles(ui.tile("Today", f"{round(minutes)} min"), ui.tile("Plays", len(plays)),
                     ui.tile("Top artist", artists.most_common(1)[0][0] if artists else "None"))]


@p.action
async def heart(ctx, id):
    h = ctx.store.setdefault("hearts", [])
    h.remove(id) if id in h else h.append(id)
    ctx.save()


@p.action
async def clear(ctx):
    ctx.store["hearts"] = []
    ctx.save()


@p.action
async def find(ctx, q):
    q = q.lower()
    hits = {x["id"]: x for x in ctx.plays(midnight(), time.time()) if q in x["name"].lower() or q in (x["artists"] or "").lower()}
    hearts = ctx.store.get("hearts", [])
    return [ui.item(x["name"], x["artists"], x["image"], right="♥" if i in hearts else None, action="heart", args={"id": i})
            for i, x in list(hits.items())[:12]]


@p.action
def test_alert(ctx):
    ctx.alert("Hello from a plugin", "This is a notch alert")

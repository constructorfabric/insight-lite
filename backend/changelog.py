#!/usr/bin/env python3
"""What's-new page (/whats-new).

A hand-maintained changelog shown under Manage. To add an entry, prepend a new
dict to CHANGELOG (newest first). Each change has a `type` — one of
security / fix / feature / redesign — which colours its pill. Keep the text
user-facing: say what changed for the person using the report, not the code.
"""
from __future__ import annotations

import html as _h

# Newest first. `date` is an ISO date string; `changes` is a list of
# {type, title, detail}. Prepend new releases at the top.
CHANGELOG = [
    {
        "date": "2026-08-13",
        "changes": [
            {"type": "fix", "title": "The score no longer rewards work nobody reviewed",
             "detail": "Most of the score measures friction — how many review rounds a "
                       "change took, how often an item bounced back, how long it waited to be "
                       "merged. Friction needs other people, so anyone merging their own work "
                       "unreviewed scored perfectly on all of it: zero review rounds read as "
                       "the cleanest work on the board, and a self-merge is never declined. "
                       "Across the last 30 days the share of a person’s merged work that "
                       "nobody reviewed predicted their score better than how much they "
                       "actually shipped. Three things changed. A pull request nobody reviewed "
                       "no longer counts as one that needed no changes — it counts as "
                       "unknown. How much of your merged work a colleague reviewed is now a "
                       "factor in Craft in its own right. And every per-item average is now "
                       "weighed by how much work stands behind it, so a ratio from three pull "
                       "requests stops outranking the same ratio from eight hundred."},
            {"type": "fix", "title": "Automated reviews no longer count as somebody reviewing you",
             "detail": "“Was this reviewed” was read from GitHub’s own review "
                       "count, which includes CodeRabbit and the other review bots — and "
                       "your own reviews of your own pull request. 36 merged pull requests "
                       "looked reviewed with no colleague involved, and 15 more had only their "
                       "author. Review rounds are now counted from people other than you, bots "
                       "excluded. This mostly helped whoever ships the most: much of the "
                       "“rework” the score had been charging them for was automated. "
                       "One person’s review rounds fell from 5.3 to 1.4 and they moved "
                       "from eighth place to second."},
            {"type": "improvement", "title": "Craft explains a rank gap with a number everyone has",
             "detail": "The Craft column used to explain “why them and not me” with "
                       "review rounds per pull request, which is now blank for anyone nobody "
                       "reviewed. It explains the gap with the share of merged work that was "
                       "reviewed instead — defined for everyone who merged anything."},
        ],
    },
    {
        "date": "2026-08-12",
        "changes": [
            {"type": "improvement", "title": "Person → Activity says what the Lines column counts",
             "detail": "The weekly table’s Lines +/- is the raw git diff: every changed "
                       "line, including generated and vendored files. That is deliberately not "
                       "the same number as the LOC in the contribution KPIs, which drops whole "
                       "files it considers noise. Neither figure separates comments or "
                       "docstrings from code — the filter works on file paths, never on "
                       "line contents. All of which is now written under the table instead of "
                       "having to be asked about."},
        ],
    },
    {
        "date": "2026-08-06",
        "changes": [
            {"type": "improvement", "title": "Formulas in the chat are typeset",
             "detail": "Asking how a metric is computed produced the raw source of the formula "
                       "— backslashes, braces and all — because nothing rendered it. "
                       "Formulas are now laid out properly, and the maths typesetter is only "
                       "fetched by a conversation that actually contains one. If it cannot "
                       "load, a readable plain-text version stays on screen rather than a gap."},
            {"type": "fix", "title": "Identifiers in the chat are no longer mangled",
             "detail": "Any metric name with underscores in it was read as formatting, so "
                       "flow_friction_per_item arrived with the middle of it in italics and "
                       "pieces missing. In an answer about metrics that is most of the words."},
            {"type": "fix", "title": "The chat says when a filter is narrowing your answer",
             "detail": "A question asked while a scope was set was answered only within that "
                       "scope, silently — and somebody who set a filter earlier and forgot "
                       "could be told there was no data about them when there was. Answers now "
                       "state the slice they cover, and when a person falls outside the current "
                       "scope the answer reports what their numbers look like without it. The "
                       "Person screen ignores the scope entirely, and now says so."},
        ],
    },
    {
        "date": "2026-08-05",
        "changes": [
            {"type": "security", "title": "The assistant can no longer read stored secrets",
             "detail": "The chat can run read-only SQL, and nothing stopped it reading the "
                       "table the portal keeps its own credentials in — so it could be asked "
                       "for the MCP access token and would have handed it over. That table is "
                       "now refused at the database level rather than by inspecting the query, "
                       "so quoting it differently, aliasing it, hiding it in a subquery or "
                       "joining to it are all refused too, and it no longer appears in the "
                       "schema the assistant is shown. Ordinary questions are unaffected."},
            {"type": "fix", "title": "The chat answers instead of going quiet",
             "detail": "A question that needed a lot of digging could come back with nothing "
                       "at all: the panel stayed empty, no answer was recorded, and the only "
                       "way to tell was to ask again. It happened four times in the logged "
                       "history, once followed a minute later by the person typing \u201cis it "
                       "stuck?\u201d. A turn that runs out of steps now says so and suggests "
                       "narrowing the question, and the underlying failure is written to the "
                       "server log instead of being swallowed."},
            {"type": "improvement", "title": "The chat gets more room when it is getting somewhere",
             "detail": "Each question had a fixed budget of eight tool calls, which was tight "
                       "for anything that needed exploring and generous for anything going in "
                       "circles. A question that keeps making progress \u2014 each call "
                       "returning something new \u2014 can now use up to fourteen, while one "
                       "repeating itself still stops at eight. It is also told when the budget "
                       "is nearly gone, so it answers with what it has rather than spending the "
                       "last call on another query."},
            {"type": "feature", "title": "Six new things the assistant can look up directly",
             "detail": "It can now rank people by activity for a window or an element, pull one "
                       "person\u2019s totals, resolve a human name to a login, report how "
                       "fresh the data is, look up a single metric\u2019s formula, and read "
                       "somebody\u2019s developer score with every signal beside the team "
                       "median. Before this it rebuilt each of those from raw SQL every time, "
                       "which is what made those questions slow and sometimes unanswerable: "
                       "\u201chow is my score built\u201d had no tool at all, and asking about "
                       "one metric downloaded the whole 91-metric catalogue. The same tools are "
                       "available to MCP clients and are listed on Manage \u2192 MCP access."},
            {"type": "fix", "title": "An answer about an element could be built from no data",
             "detail": "Repositories are identified two ways \u2014 as \u201corg/name\u201d "
                       "and as the bare name \u2014 and the assistant sometimes matched on the "
                       "wrong one. That does not fail: it quietly finds nothing, so a question "
                       "about an element could be answered from an empty result. The schema it "
                       "reads now states which identifier joins, and says plainly that the "
                       "other one returns nothing rather than an error. A query that does fail "
                       "now comes back with the real table and column names instead of a bare "
                       "message."},
            {"type": "redesign", "title": "The score screen reads like a score",
             "detail": "The band scale is now a continuous red-to-green bar with the band names "
                       "on it, the boundaries above and a marker for where you are, so which "
                       "end is better needs no explaining. \u201cWhat\u2019s changed\u201d "
                       "splits a move into the part that came from your own numbers and the "
                       "part that came from everyone else\u2019s, in the same unit, so the two "
                       "add up to the total. Pillars appear in one order everywhere, the change "
                       "table is points only and every column reconciles, and roughly two "
                       "hundred words of explanation are gone \u2014 what remains is behind "
                       "the same \u201c?\u201d the period and scope controls use. On a wide "
                       "screen the panel now uses the width: your score on the left, the team "
                       "standing beside it."},
            {"type": "fix", "title": "Score changes were the wrong colour in dark mode",
             "detail": "Red and green figures on the score panel were drawn in the light "
                       "theme\u2019s colours whatever theme you were using, which in dark mode "
                       "left them at 2.9:1 against the panel \u2014 below the readable "
                       "minimum. They now follow the theme, at 4.8:1."},
            {"type": "feature", "title": "Sort the weekly table on Person \u2192 Activity",
             "detail": "Click any column heading to sort: the week, commits or lines for a "
                       "repository, or issues opened. Clicking the week heading puts it back in "
                       "date order, which is where it starts. Lines sort by the total changed, "
                       "added plus deleted."},
        ],
    },
    {
        "date": "2026-08-04",
        "changes": [
            {"type": "redesign", "title": "The developer score, rebuilt around one number",
             "detail": "The panel now follows the shape a credit score uses: the number with "
                       "its band, the pillars it is made of, and a drill from each pillar to "
                       "the factors underneath, with each factor shown against the team median. "
                       "It also answers \u201cwhy did it move\u201d by separating how much of "
                       "a change was the person and how much was everyone else moving around "
                       "them \u2014 the score is a rank, so both count. Still marked "
                       "experimental."},
            {"type": "fix", "title": "The score bands were harsher than anyone had checked",
             "detail": "The boundaries between Building, Developing, Solid and Strong had never "
                       "been measured against real numbers. On a year of production data 41% of "
                       "the people who get banded fell under the lowest boundary and 7% reached "
                       "the top one \u2014 a scale that called four people in ten "
                       "\u201cBuilding\u201d. The boundaries are now 30 / 50 / 70, which "
                       "spreads the same people 11 / 39 / 36 / 15%, and they are configurable "
                       "with an auto-calibrate that fits them to your own distribution."},
            {"type": "improvement", "title": "Flow and Delivery read the rows that matter",
             "detail": "The board-history table is a daily photograph of every tracked item, so "
                       "almost all of it is the same status recorded again. Two metrics only "
                       "ever need where a status changed and where an item currently stands "
                       "\u2014 1,816 and 1,533 rows out of 141,141 \u2014 and they were "
                       "reading all of it, with the cost growing every day. Those rows are "
                       "materialised now, so the pages stay fast as history accumulates."},
        ],
    },
    {
        "date": "2026-07-31",
        "changes": [
            {"type": "improvement", "title": "Charts load faster and weigh less",
             "detail": "Every chart in the report and on dashboards is drawn by a new engine, "
                       "bundled with the page instead of fetched as a separate 800 KB library. "
                       "Pages that draw no charts at all — Delivery, People, Person, "
                       "Repositories, Elements, Traffic, AI tools — stop downloading it "
                       "entirely. Same charts, same colours, same hover tooltips; the only "
                       "visible change is that a legend no longer appears twice under the "
                       "per-company trends."},
            {"type": "improvement", "title": "Delivery opens in under a tenth of a second",
             "detail": "The board-state panel ran a query that read most of the snapshot table "
                       "to find each item's latest status. With the right index it is 185x "
                       "faster and the page went from 1.8s to under 0.1s. Flow got the same "
                       "treatment — it was reading that table three times per request and now "
                       "reads it once."},
        ],
    },
    {
        "date": "2026-07-29",
        "changes": [
            {"type": "fix", "title": "A company keeps the same colour from one report to the next",
             "detail": "Company colours were handed out in order of commit volume: the busiest "
                       "company took the first colour, the next one the second, and so on. Two "
                       "companies therefore swapped colours the moment they swapped places — so "
                       "comparing this week's chart against last week's could mean comparing two "
                       "different companies with nothing saying so. A colour is now derived from "
                       "the company's name, so it stays put whatever the numbers do. The same "
                       "company also gets the same colour everywhere: the contributors chart, the "
                       "company table, the trend breakdowns and the dots on Manage → Identity each "
                       "used to pick from their own list, so one company could be purple in one "
                       "panel and amber in another on the same page. Colours have therefore "
                       "changed once, now, and should not change again. If a company should keep "
                       "a specific colour, pin it on Manage → Config under Companies → Chart "
                       "colour: the swatch shows the current colour, a picker sets it, and "
                       "“reset” goes back to the generated one. Pins are stored in the database, "
                       "so they survive a deployment."},
        ],
    },
    {
        "date": "2026-07-28",
        "changes": [
            {"type": "fix", "title": "The identity roster no longer has a YAML copy",
             "detail": "Saving on Manage → Identity used to write a people.yaml file and keep 50 "
                       "dated copies of it, alongside the database that actually holds the "
                       "roster. When a roster genuinely had to be restored, every one of those 50 "
                       "copies turned out to be test data — two invented people — because the "
                       "test suite had been saving over them. Worse, the file was read back in on "
                       "startup whenever the stored roster looked empty, which is how an invented "
                       "person (“alice”, with no commits and no pull requests) and a "
                       "non-existent repository ended up in the live curated data. The file, its "
                       "backups and the code that read them are gone: the database is the only "
                       "copy, and the database snapshot taken before every deploy is what a "
                       "restore uses. The Update page's “Regenerate editors” button no longer "
                       "claims to rebuild backups, because there are none to rebuild. Nothing "
                       "about editing or saving identity changed otherwise."},
            {"type": "fix", "title": "Hover tooltips are back",
             "detail": "The little explanations that appear when you hover a column header, a "
                       "person's name or a chart segment stopped appearing when the report moved "
                       "to the new frontend — the cursor still turned into a question mark and "
                       "the dotted underline still promised an explanation, but nothing showed. "
                       "They work again everywhere they used to: on /people alone there are over "
                       "400 of them."},
            {"type": "fix", "title": "“Open → first review” and “Review → merge” show numbers again",
             "detail": "Two of the five cycle-time figures on the Flow page had been blank since "
                       "they were added — not empty for this window, but impossible to compute, "
                       "because they were measured from a review REQUEST that is never recorded. "
                       "They now measure from the first review actually submitted, which is also "
                       "what they claim to show: how long until a human looked, rather than how "
                       "long until a reviewer was auto-assigned. And when a cycle figure genuinely "
                       "has no data in the selected window, it is now named underneath instead of "
                       "quietly disappearing, so a missing number can be told apart from a "
                       "missing measurement."},
            {"type": "feature", "title": "Discord and Telegram handles on identity records",
             "detail": "Manage → Identity now has a Discord and a Telegram field on each "
                       "person, next to name and company, so the roster can carry where to "
                       "reach someone and not only how their commits are attributed. Paste "
                       "the handle with or without the leading @ — it is stored without. "
                       "They are contact details only: they play no part in matching commits "
                       "to people, and they do not affect identity confidence or any metric. "
                       "The person filter searches them too."},
            {"type": "fix", "title": "The Developer score says when it cannot be shown",
             "detail": "If the score could not be worked out for someone, the whole panel "
                       "used to disappear from their page, which was indistinguishable from "
                       "them having no score. It now shows a single line explaining which of "
                       "the two it is — nobody active enough in this window, or a failure "
                       "worth reporting."},
            {"type": "fix", "title": "The report can no longer freeze quietly on old numbers",
             "detail": "The report is rebuilt from the database whenever the data changes, and if "
                       "that rebuild failed the portal kept serving the last version that worked "
                       "— every page answering normally, with numbers that had silently stopped "
                       "moving. Serving the last good version is still what happens, because a "
                       "readable report beats an error page, but it is now reported: the health "
                       "check used for monitoring fails and says the report has stopped "
                       "rebuilding, and it recovers on its own once a rebuild succeeds. Nothing "
                       "changes on any page while the report is building normally."},
            {"type": "feature", "title": "Flow shows why pull requests were closed without merging",
             "detail": "A new “Closed without merging” block on the Flow page: how many of the "
                       "pull requests closed in the selected period ended without a merge, and "
                       "why — the author withdrew it after review, withdrew it before anyone "
                       "looked, never finished it, somebody else rejected it after review, or "
                       "nobody ever reviewed it before it was closed. The reason is worked out "
                       "from who closed it, not guessed. Each bucket opens the list behind it, "
                       "and there is a per-repository breakdown plus the longest-ignored pull "
                       "requests by name. An author closing their own work after feedback is "
                       "feedback working, so it is shown as context with no target attached; the "
                       "one worth acting on is the pull request nobody ever looked at. None of "
                       "this affects the Developer score."},
            {"type": "feature", "title": "Flow now shows work in flight",
             "detail": "A new “In flight” block on the Flow page shows the pull requests that "
                       "are open right now: how many nobody has reviewed yet, the median and "
                       "oldest age, how many have been open over 30 days, and how many are still "
                       "drafts — plus who is carrying them, oldest first. Previously a person "
                       "deep in a long-running PR looked idle, because only work merged to the "
                       "default branch is counted anywhere. Every number opens the list of PRs "
                       "behind it. Two things worth knowing: this block deliberately ignores the "
                       "period selector (open work is a right-now quantity, so it does not change "
                       "when you switch to 7 or 30 days), and open work is NOT added to any "
                       "commit, code or delivery number and does not affect the Developer score — "
                       "it is work in progress, not output. The block also lists the pull requests "
                       "still waiting on a first review after a week, longest wait first, and the "
                       "typical size of open work — median and p90 with the biggest named, never a "
                       "total, since one fork-sync pull request can be a third of the whole."},
            {"type": "fix", "title": "“Show all” on the Developer score board works again",
             "detail": "On a person's page the team board lists the top 15 with a “Show all N” "
                       "button underneath, and clicking it did nothing — the rest of the ranked "
                       "people stayed hidden. The button now expands the full board as it "
                       "always did. Everything else on the page is unchanged."},
        ],
    },
    {
        "date": "2026-07-27",
        "changes": [
            {"type": "fix", "title": "The nightly refresh runs again",
             "detail": "Renaming the \"user stories\" count to \"features\" changed the stored "
                       "shape of the per-person history but left existing databases on the old "
                       "column, so every refresh failed at the save step and the report kept "
                       "showing the last successful day. Existing databases are now upgraded in "
                       "place on startup, with all historical counts preserved."},
        ],
    },
    {
        "date": "2026-07-23",
        "changes": [
            {"type": "improvement", "title": "Each report view now has its own page and link",
             "detail": "Overview, Trend, Delivery, Flow, People, Person, Repositories, Elements, "
                       "Traffic and AI tools are now separate pages with their own URLs (e.g. "
                       "/trend, /people) — so you can bookmark or share a specific view and the "
                       "browser Back button moves between them. The period and slice filters stay "
                       "in the URL too, so a shared link reopens the exact same state. Old links "
                       "like /report#trend redirect automatically. The combined 'Full report' view "
                       "is retired — everything it showed now lives on the individual pages. Every "
                       "screen looks exactly as it did before."},
        ],
    },
    {
        "date": "2026-07-22",
        "changes": [
            {"type": "improvement", "title": "Clearer Trend layout",
             "detail": "The breakdown switcher (company / work type / repo type / element) now "
                       "sits with the two stacked charts it actually re-splits, under a "
                       "'Breakdown' heading; throughput, time-to-merge and contributors move to a "
                       "'Throughput & activity' section. Granularity still applies to every chart."},
            {"type": "improvement", "title": "Report charts use the same engine as dashboards",
             "detail": "The report's trend, throughput and contributor charts now render with "
                       "Vega-Lite too — one consistent chart engine across the whole product, "
                       "with real legends and hover tooltips. Numbers and tables are unchanged."},
            {"type": "improvement", "title": "Sharper, interactive dashboard charts",
             "detail": "Dashboard charts are now drawn with a proper charting engine (Vega-Lite), themed "
                       "to Insight's look — cleaner axes, real legends and hover tooltips — while numbers "
                       "and tables are unchanged. Groundwork for richer chart types."},
            {"type": "improvement", "title": "More metrics available in dashboards",
             "detail": "The widget picker now lists every real metric a tool reports (e.g. "
                       "total PRs, total/closed issues) — previously some were hidden unless "
                       "their internal field name happened to match the metric registry. Only "
                       "internal sample-size counters stay out of the list."},
            {"type": "improvement", "title": "Chart any metric — combine numbers on one chart",
             "detail": "Single-number metrics (bugs, PRs, defect rate, …) can now go on a "
                       "column, bar, pie or table widget too — pick several and each becomes "
                       "a bar or slice (the BI 'measure values' pattern), so the whole metric "
                       "catalogue is usable in charts, not just as counters."},
            {"type": "improvement", "title": "Choose how each widget looks — and combine metrics",
             "detail": "Dashboard widgets now separate the metric from how it's shown: pick a "
                       "display type (number, line, area, column, bar, pie, table) and switch it "
                       "any time. Time-series widgets can carry several metrics on one chart. "
                       "Also fixes charts that rendered as a solid block on dashboards."},
            {"type": "improvement", "title": "Easier way to add dashboard widgets",
             "detail": "Adding a widget is now pick-what-to-show: a searchable list of "
                       "metrics grouped by category, instead of typing internal field names. "
                       "The display type is chosen for you (with a toggle), the title is "
                       "prefilled, and you see a live preview. An advanced option lets you "
                       "pick a raw tool and then choose its field from a dropdown (with a "
                       "custom-entry fallback) — no more remembering internal names. The "
                       "dashboard view and editor now sit in the normal report layout with "
                       "the sidebar."},
            {"type": "feature", "title": "Build your own dashboards",
             "detail": "A new Dashboards area lets you assemble your own dashboard from the report's "
                       "building blocks: add panels (a KPI, a table, a chart) bound to a metric or tool, "
                       "arrange them by drag, and share a link. Panels re-slice live by period and scope "
                       "like the main report. Open it from the sidebar → Dashboards → New."},
            {"type": "improvement", "title": "Expand the assistant to a large window",
             "detail": "The chat panel has an expand button that grows it to about 80% of "
                       "the browser window for reading longer answers and tables, and a "
                       "restore button to shrink it back to the corner."},
            {"type": "fix", "title": "The assistant always answers, even on hard questions",
             "detail": "On a complex follow-up (e.g. comparing two people) the assistant "
                       "could spend its whole tool budget exploring and never write a reply, "
                       "leaving an empty answer. It now always finishes with a written answer "
                       "from what it gathered. It also no longer wastes steps trying an "
                       "unsupported per-person slice or guessing table names — it uses the "
                       "person lookup and checks the schema first, so answers come faster and "
                       "cost less."},
            {"type": "improvement", "title": "Assistant renders tables",
             "detail": "Answers that include a table now show a proper table in the chat "
                       "instead of raw text with pipes."},
        ],
    },
    {
        "date": "2026-07-21",
        "changes": [
            {"type": "improvement", "title": "The assistant knows it’s you",
             "detail": "When you’re signed in, first-person questions now resolve to you — "
                       "“how many commits have I made?”, “show my open PRs”, “what’s my review "
                       "latency?” — without spelling out your login. Your identity is taken "
                       "from the signed-in session on the server, never from the page, and only "
                       "your own login is used; no one else’s identity is exposed."},
            {"type": "feature", "title": "Ask the metrics assistant",
             "detail": "A chat panel now lives in the corner of the report. Ask a question "
                       "in plain language — “why did PR merge rate fall for Insight?”, “which "
                       "company contributed the most commits?” — and it answers from the live "
                       "report data, using the same read-only tools the MCP server exposes. "
                       "Every figure in an answer comes from a real query, and metric "
                       "definitions are cited from the catalog, so numbers can’t be made up. "
                       "It knows what you’re looking at: the current slice, period and tab are "
                       "passed in, so answers stay scoped to your view. Answers stream in and "
                       "render Markdown. Runs on Gemini — set GEMINI_API_KEY to enable it."},
            {"type": "feature", "title": "Usage insights tracks the assistant",
             "detail": "The Usage insights page now has a Metrics assistant section: panel "
                       "opens, questions asked, unique askers, tokens used and estimated cost "
                       "over any period, plus which report view each question was asked from. "
                       "Click “questions asked” for the full request log, a view for its "
                       "questions, or a person to see what they asked — each row shows when, "
                       "who, view, tokens and cost. No question text is stored. Cost is shown "
                       "only when per-token prices are configured; otherwise it reads n/a."},
            {"type": "feature", "title": "Usage insights — how the report itself is used",
             "detail": "A new Usage insights page under Manage shows how the report is "
                       "actually used: how many times it was opened, how many distinct "
                       "people opened it, which tabs and which widgets they viewed, and a "
                       "per-person breakdown — over any period. It also tracks which "
                       "drill-downs people actually open. Every row is clickable: open a "
                       "widget, tab or drill to see who viewed it, or a person to see what "
                       "they viewed. Opens are counted on the "
                       "server (reliable); tab, panel and drill views come from the browser and "
                       "are a floor, not exact. Signed-in viewers are attributed to their "
                       "person automatically; whole-report All-tab scrolls are tracked "
                       "separately so they don’t make every widget look “seen”."},
        ],
    },
    {
        "date": "2026-07-17",
        "changes": [
            {"type": "improvement", "title": "Removed the Commit mix panel from Overview",
             "detail": "The code-vs-specs split came off the Overview — the KPI tiles "
                       "(commits, spec edits) and % by category already convey the shape "
                       "of the work, so it was one panel too many."},
            {"type": "improvement", "title": "Period and Slice share one row on desktop",
             "detail": "On wide screens the two filter controls now sit on a single row — "
                       "the repository Slice pinned left, the Period presets to the right — "
                       "instead of stacking, so the sticky filter bar is shorter. The "
                       "explanatory legend moves just below; mobile keeps the stacked layout."},
            {"type": "improvement", "title": "Data health moved to its own Manage page",
             "detail": "The “review before sharing” trust tiles left the dashboard for a "
                       "dedicated Data health page under Manage. It keeps the tiles "
                       "(unresolved identities, unclassified repos, traffic access, "
                       "API rate-limit) and adds the shape of the dataset — org, last "
                       "collection, repos analysed (primary vs legacy, platform vs app), "
                       "contributors (members vs external), identity resolution, and "
                       "headline totals. The dashboard stays about the work; data-quality "
                       "lives with the other admin tools."},
            {"type": "feature", "title": "Developer score — team panel on Overview",
             "detail": "Overview gains a team developer-score rollup that follows the "
                       "selected period: the band split (Strong / Solid / Developing / "
                       "Building), a top-N leaderboard teaser (click a name for the full "
                       "breakdown), per-company median scores, and the team's real per-pillar "
                       "medians (time-to-merge, review rounds, friction). Because the score is "
                       "org-relative its median sits mid-scale by design, so the panel leans on "
                       "the spread and the concrete medians rather than a single headline number."},
            {"type": "improvement", "title": "Score leaderboard shows the whole team",
             "detail": "The developer-score leaderboard used to stop at the top 15. It now "
                       "carries every ranked person; the top 15 show by default with a "
                       "“Show all N” button to reveal the rest — so you can find "
                       "yourself and mid-table people, not just the leaders."},
            {"type": "improvement", "title": "Score leaderboard compares everyone to you",
             "detail": "On your Person page, expanding a teammate on the score leaderboard now "
                       "compares them to you, not to their neighbour — so it reads as how to "
                       "catch up (for those ahead) or where you lead (for those behind), e.g. "
                       "“Behind you by 4 pts — mostly Delivery: they 15h, you 0.3h.” The gauge "
                       "at the top still points to your own next step up. When you open someone "
                       "else's page, the comparison is against them by name."},
            {"type": "feature", "title": "Export any drill-down to CSV",
             "detail": "Every drill-through — the rows behind a number: commits, PRs, "
                       "issues, people, CI runs, flow items, board rewinds — now has an "
                       "Export CSV button. It downloads the full result set with the same "
                       "filters (period, slice, author, flag…), not just the rows scrolled "
                       "into view, and includes the GitHub URL per row. The file is UTF-8 "
                       "with a byte-order mark so names open correctly in Excel."},
            {"type": "improvement", "title": "Person tab opens on you",
             "detail": "When you're signed in, the Person tab now defaults to your own "
                       "page — no need to find yourself in the picker first. It resolves "
                       "your GitHub login from the sign-in, and quietly does nothing if it "
                       "can't match you to a contributor. An explicit person link (or picking "
                       "someone) always wins."},
            {"type": "improvement", "title": "Developer score — explains your rank in real terms",
             "detail": "The developer score now shows why you rank where you do and ties every "
                       "pillar to real work. Each person breaks down as a chain — your actual "
                       "numbers (commits, PRs, hours-to-merge, review rounds) → how that compares "
                       "to the team median → the pillar's 0–100 score → the points it adds — and "
                       "the points sum exactly to the total. A plain-language line names why "
                       "you're behind the person just above you (“mostly Delivery: you merge in "
                       "40h vs their 9h”). The team leaderboard stays compact — rank, name, a bar "
                       "showing what the score is made of, and the total — and any row expands to "
                       "the full breakdown. Everyone active is ranked; a scored pillar with no "
                       "data counts as 0 (a real minus), and a pillar too sparse across the team "
                       "is left out for everyone."},
            {"type": "feature", "title": "Four more MCP tools — trends, flow, drill-downs, metric definitions",
             "detail": "The MCP server gained: trend (commits/LOC/PR time series over a "
                       "window, by company/work-type/repo-type/element), flow (delivery-flow "
                       "health — reopen/bounce rates, cycle-time segments, per-person "
                       "friction), list_items (the individual commits/PRs/issues behind a "
                       "number, with GitHub links), and metrics_catalog (every metric's exact "
                       "definition and formula). See them all under Manage → MCP."},
            {"type": "feature", "title": "MCP page — connection details and the tool catalog",
             "detail": "MCP now has its own page under Manage → MCP: the connection URL and "
                       "bearer token to wire up an AI client (moved out of the Update page), "
                       "plus a catalog of every read-only tool the server exposes with its "
                       "parameters and description. The tool list is read straight from the "
                       "MCP server, so it always matches what a connected client actually sees."},
            {"type": "fix", "title": "MCP page no longer shows “unauthorized”",
             "detail": "The new Manage → MCP page briefly returned “unauthorized”: the proxy "
                       "was routing every path starting with /mcp — including the /mcp-info "
                       "page — to the token-gated MCP protocol endpoint. The proxy now matches "
                       "the protocol endpoint exactly, so the page loads normally while the "
                       "MCP connection keeps its own token gate."},
            {"type": "feature", "title": "Sort any breakdown table by any column",
             "detail": "Click a column header in any breakdown table — the per-person table, "
                       "company, by-element, reviewers, repo inventory, work-type by company "
                       "and by repo, AI-tool usage, and the code-marker tables — to sort by "
                       "it; click again to reverse. Sorting is by the real value, so LOC "
                       "sorts by the actual number and percentages sort numerically, not by "
                       "how they’re displayed; text columns sort case-insensitively. Sorting "
                       "the per-person table reveals every person (not just the top 40) so the "
                       "order covers everyone."},
            {"type": "feature", "title": "The report is always up to date",
             "detail": "The report page is now generated fresh from the database each "
                       "time you open it, instead of from a file that had to be rebuilt "
                       "after every collection or edit. Change the taxonomy, fix an "
                       "identity, or run a collection and the numbers reflect it on your "
                       "next visit — no “did it re-render?” step."},
            {"type": "feature", "title": "Loading indicator while data loads",
             "detail": "A slim progress bar now appears at the top of the page whenever "
                       "a panel is fetching — switching period or slice, opening Delivery/"
                       "Flow, or drilling in. Since these load from the database rather "
                       "than instantly, the bar makes it clear the report is working, not stuck."},
            {"type": "fix", "title": "Quieter, cleaner server logs",
             "detail": "Switching period or navigating away while a panel is still "
                       "loading used to leave a scary error trace in the server log, even "
                       "though nothing was actually wrong — the browser had simply moved on. "
                       "Those harmless client-disconnects are now recorded as a single tidy "
                       "line, while genuine errors keep their full detail. No change to what "
                       "you see in the report."},
        ],
    },
    {
        "date": "2026-07-16",
        "changes": [
            {"type": "redesign", "title": "One taxonomy: Bugs, Epics, Features",
             "detail": "Issue categories across the whole report now come from a single "
                       "source — the taxonomy you edit under Manage → Taxonomy — instead of a "
                       "separate frozen rule set for the Overview. The Overview, company and "
                       "per-person tables, and Person pages now show Bugs, Epics and Features "
                       "(the “User stories” tile is gone — the team barely uses story labels; "
                       "epics are broken out on their own). Editing the taxonomy now re-tags "
                       "every issue and refreshes these numbers with no re-collect. Two data "
                       "fixes came with it: the `pr-issue` link label no longer counts as a bug "
                       "(it was inflating the bug count several-fold), and bug counts now catch "
                       "every issue that resolves to “bug”, not only those found at collection "
                       "time — so the numbers shift when this ships."},
            {"type": "redesign", "title": "Delivery and Flow, untangled",
             "detail": "The two tabs now split cleanly by question. Delivery answers “what "
                       "shipped and where is it now” — issues, PRs, CI, and the current board "
                       "state. Flow answers “how did work move and how long did it take” — "
                       "friction and cycle-times, plus a Board-movement group (cumulative flow, "
                       "time in stage, and QA→dev returns) that used to be scattered on "
                       "Delivery. Each metric now lives in exactly one place."},
            {"type": "feature", "title": "Time in stage — how long work waits between statuses",
             "detail": "The Flow tab now shows, per board stage, how long items sit before "
                       "moving on: the age of what's waiting right now (from each item's last "
                       "update — available immediately) and the completed dwell time from past "
                       "moves. GitHub keeps no board-status history, so this is reconstructed "
                       "from board snapshots taken every few hours and sharpens as they "
                       "accumulate."},
            {"type": "feature", "title": "Delivery KPIs get sparklines and period deltas",
             "detail": "Every Delivery tile (issues, PRs, CI) now carries a mini-trend and a "
                       "“▲/▼ N% vs the previous period” pill, like the Overview KPIs. Colours are "
                       "outcome-aware — for “lower is better” metrics (defect rate, abandoned, "
                       "reverts, time-to-merge, review time, CI duration) a rise shows red, a "
                       "fall green."},
            {"type": "feature", "title": "Review & merge timing you can drill into",
             "detail": "Added median time-to-first-review (review requested → first review) to "
                       "the PR tiles, and every PR in a drill now shows its own time-to-merge. "
                       "The Delivery tab is also reordered by lifecycle: Issues → Pull requests → "
                       "CI & gates → Workflow."},
            {"type": "redesign", "title": "Rebuilt the Identity editor",
             "detail": "Identity is now a list + detail-panel layout that leads with a "
                       "“Needs attention” queue — people with no company, a likely duplicate, "
                       "or a low-confidence identity float to the top, grouped, so you work the "
                       "backlog instead of scanning one big table. Duplicate accounts merge "
                       "inline from the person's panel with a preview of what folds together "
                       "(the separate suggestions block is gone), bots are integrated into the "
                       "same list with a Human/Bot toggle instead of their own table, and you "
                       "can tick several people to assign a company or mark them bots in bulk. "
                       "Same save-and-apply, merges and overrides underneath."},
        ],
    },
    {
        "date": "2026-07-15",
        "changes": [
            {"type": "feature", "title": "Board flow over time — Cumulative Flow Diagram",
             "detail": "The Delivery tab gains a “Board flow over time” chart next to the "
                       "current-state Workflow strip: a stacked area of how many board items sit "
                       "in each stage (Backlog → … → Done/Released) on each day. Widening upper "
                       "bands mean growing WIP or backlog; a fattening QA band is a testing "
                       "bottleneck; a rising base is throughput to Done. Built from daily board "
                       "snapshots, so it’s forward-only from when snapshots began and sampled "
                       "once a day (GitHub keeps no board status history)."},
            {"type": "feature", "title": "Returned to dev from testing + rework rounds",
             "detail": "Two new rework signals on the Flow tab. “Returned to dev from testing” "
                       "counts items pushed backward on the board from QA to development, "
                       "reconstructed by diffing consecutive daily board snapshots — click the "
                       "tile for the exact items and owners. “Rework rounds” counts how many "
                       "times a reviewer explicitly sent a PR back with a changes-requested "
                       "review — the closest proxy we have to review→fix cycles, shown per person "
                       "and org-wide."},
            {"type": "feature", "title": "New Flow tab — “friction” explained, plus lifecycle metrics",
             "detail": "The Developer score’s Flow pillar reported a “friction/item” number with "
                       "nowhere that said what it meant. There’s now a dedicated Flow tab that "
                       "spells it out — friction = 2×(back-to-draft + reopened) + review-request "
                       "and assignment churn, per owned item, lower is smoother — with a worked "
                       "example. It also surfaces the metrics the issue/PR timeline stream makes "
                       "possible: reopen, back-to-draft and re-review rates, and cycle-time "
                       "medians between real lifecycle events (open → first review → merge, "
                       "draft → ready, open → close), plus a per-person breakdown. Follows the "
                       "period and slice like the rest of the report. These are lifecycle "
                       "segments from real events, not board-column dwell time (GitHub keeps no "
                       "status-change history)."},
            {"type": "feature", "title": "Shareable links — the view is in the URL",
             "detail": "The report now writes your current view into the address bar — the "
                       "period (preset or custom range), the repository slice, the open Person, "
                       "and the tab. Copy the link and whoever opens it lands on exactly what "
                       "you saw, no “now pick this, then that” instructions. Links are restored "
                       "on load and the URL updates as you change filters."},
        ],
    },
    {
        "date": "2026-07-14",
        "changes": [
            {"type": "redesign", "title": "Rebuilt the Trend tab",
             "detail": "The Trend tab dropped its per-company bar strips for real charts on a "
                       "shared time axis. Contribution is now a stacked-area chart — each "
                       "band stacks up so you read both the overall momentum and the "
                       "composition at a glance — and you can break it down by company, work "
                       "type, repo type or element. Commits and meaningful LOC are separate "
                       "charts now (no more toggle). Three more trends join them: PR "
                       "throughput (opened vs merged), median time-to-merge, and active "
                       "contributors. The time axis has a granularity control that auto-scales "
                       "to the window (week → daily, quarter → weekly, year → monthly, "
                       "all-time → quarterly) and can be overridden to day / week / month / "
                       "quarter. Every chart follows the period and slice, with a labelled "
                       "hover on any bucket."},
            {"type": "feature", "title": "Workflow state on Delivery",
             "detail": "The Delivery tab now shows the current workflow state — every work item's "
                       "latest Projects v2 status, mapped through the taxonomy onto the pipeline "
                       "(Backlog → Ready → In progress → In review → QA → Done → Released) as a "
                       "stage-by-stage pipeline with counts and shares. Click any stage to drill "
                       "into the exact work items sitting in it (ref, title, repo, type, status). "
                       "It's a now-snapshot (status history isn't in "
                       "GitHub's API); unmapped statuses are flagged so you can refine the mapping."},
            {"type": "redesign", "title": "Reworked Metrics catalog",
             "detail": "The /metrics page is now a searchable reference instead of a wall of code: "
                       "compact rows (name · type · meaning) that expand to the formula and exact "
                       "query on demand, a search box, a direct/computed filter, and group jump-links. "
                       "It also now covers every panel the report shows — repo coverage, external "
                       "contributors, surviving-window, Studio provenance, Gears usage and weekly "
                       "activity were added, so the catalog is complete (58 metrics)."},
            {"type": "feature", "title": "Custom repository types",
             "detail": "The platform-vs-app axis is now open-ended: define your own repository "
                       "types (SDK, Infra, Docs, …) in Config, each with a colour, and the "
                       "report's “where effort goes” split becomes N-way everywhere — the split "
                       "panel, the per-person Type-mix column, and the Person page all show every "
                       "type, and each segment drills into its commits/PRs. Pick which type is the "
                       "default (the fallback for unlisted repos); existing platform/app configs "
                       "carry over unchanged."},
            {"type": "redesign", "title": "Reworked Config page",
             "detail": "Config now manages repository types and product elements side by side, "
                       "with a live split preview, a searchable repo list you can group by type or "
                       "element and bulk-assign, and colour-coded elements (matching the report). "
                       "Repo type and element changes apply to the report instantly on Save."},
            {"type": "feature", "title": "New home: insight.example.com",
             "detail": "The report has moved to https://insight.example.com. The old address "
                       "redirects there, so existing links keep working; update any bookmarks and "
                       "MCP client configs (the /mcp endpoint is now insight.example.com/mcp)."},
        ],
    },
    {
        "date": "2026-07-13",
        "changes": [
            {"type": "feature", "title": "Guided taxonomy setup",
             "detail": "The Taxonomy page is now a step-by-step wizard: pick the scope (the "
                       "base everyone inherits, or an override for one org / element / repo / "
                       "project), then work through categories, the flow pipeline and CI. Labels "
                       "are triaged by volume — confirm the auto-matched guesses, decide the "
                       "high-volume ambiguous few, leave the rare tail; a live coverage meter "
                       "shows the share of issues that land in a real category. Board statuses "
                       "drag onto an ordered Backlog-to-Released pipeline. Work-types now mirror "
                       "GitHub's own vocabulary — Bug / Feature / Task / Epic (plus Spec / Docs / "
                       "Test). The dense grid is still there as the Advanced editor, and narrow "
                       "scopes save only what differs, so inheritance is preserved."},
            {"type": "redesign", "title": "Clearer code-vs-specs commit mix",
             "detail": "The commit-mix panel dropped its half-empty donut for a full-width "
                       "stacked bar plus two stat cards (code / specs — percentage and commit "
                       "count), both of which drill into the commits behind them. A leftover CSS "
                       "rule was also blowing up the words 'specs' and 'code' in the caption — "
                       "fixed."},
            {"type": "redesign", "title": "New look for the whole report",
             "detail": "The report has been rebuilt around a Modern-SaaS design: the Plus "
                       "Jakarta Sans typeface, an indigo accent with per-category colours, "
                       "rounded cards and soft shadows, KPI tiles with icon-chips, delta pills "
                       "and a sparkline on every KPI, pill-style filters, and a refreshed "
                       "sidebar with icons and a logo mark. The layout now uses the full screen "
                       "width instead of a fixed column."},
            {"type": "redesign", "title": "Branded sign-in screen",
             "detail": "The GitHub login and the no-access pages were redesigned to match — "
                       "the same Jakarta typeface, indigo gradient logo and buttons, and a "
                       "friendly 'you don't have access yet' message instead of a raw 500 error."},
            {"type": "security", "title": "Optional GitHub-org login (opt-in)",
             "detail": "You can now put the portal behind GitHub sign-in that admits members "
                       "of the connected org and refuses everyone else, with basic-auth kept as a "
                       "break-glass fallback. It runs as an opt-in oauth2-proxy sidecar (compose "
                       "profile 'oauth'); /mcp keeps its own bearer token. One-time setup — a "
                       "GitHub OAuth App + an nginx swap — is in deploy/oauth/README.md. Off until "
                       "you enable it."},
            {"type": "redesign", "title": "Manage pages match the new look",
             "detail": "The Manage-section pages — Update, Identity, Config, Taxonomy, "
                       "Setup, Metrics and What's new — now share the report's Modern-SaaS "
                       "look: the Plus Jakarta Sans typeface, the indigo accent, and rounded "
                       "cards, pill buttons and focus rings. They previously carried a leftover "
                       "GitHub-style palette and the system font. The typeface is served "
                       "same-origin from /assets so these pages stay small."},
        ],
    },
    {
        "date": "2026-07-11",
        "changes": [
            {"type": "feature", "title": "Drill down from a number to the rows behind it",
             "detail": "Click almost any count — a KPI, a company or By-Element row, a person's "
                       "activity cell or their Person-page KPIs, the platform/app split, or a "
                       "Delivery tile (issues opened, defect rate, PRs opened) and issue-category "
                       "row — to open a modal listing the underlying commits/PRs/issues for the "
                       "active period and slice, scoped to that company / element / person / "
                       "classification / category. Each row links straight to that item on GitHub."},
            {"type": "redesign", "title": "Consistent number formatting everywhere",
             "detail": "One set of rules across the whole report: counts always group digits "
                       "(5,792 · 1,746), code-volume (LOC) is always compact (3.48M · 399.5K) "
                       "instead of a mix of full, comma and KLOC forms, percentages drop the "
                       "trailing '.0' (50%, 72.1%), and durations read the same in tiles and "
                       "drill-downs (1m47s). The drill-down modal groups digits too. Backed by "
                       "shared num/loc/pct/dur formatters and a guard test."},
            {"type": "feature", "title": "Drill-down loads all rows as you scroll",
             "detail": "The drill modal no longer stops at the first 500 — it lazy-loads the next "
                       "page as you scroll to the bottom, so you can reach every commit/PR/issue "
                       "behind a number. The header shows progress (e.g. '1,200 of 5,792 commits') "
                       "until everything is loaded."},
            {"type": "redesign", "title": "Drill-down modal widens to fit content",
             "detail": "The drill modal used to cap at a fixed width and squeeze/ellipsize the "
                       "title column even on big screens. It now grows to fit its content up to "
                       "80% of the viewport (and stays readable on mobile), so wide rows — long "
                       "PR/issue titles especially — aren't cramped or needlessly scrolled."},
            {"type": "feature", "title": "More numbers are drillable",
             "detail": "Extended drill-down to the work-type rows (by commit type), the "
                       "code/specs commit-mix, AI-marked commits and the per-tool split, the "
                       "Delivery PR tiles (merge rate, abandoned, reverts), the AI% / AI✦ columns "
                       "and the %-by-category rows, the reviewed-PR tiles (Code-review coverage "
                       "and Delivery 'reviewed' list the PRs that got a review), the "
                       "median-time-to-merge tiles (Code review and the by-element Med TTM open "
                       "the merged PRs behind the median), the median-PR-size tile (the PRs it's "
                       "measured over), the CI pass-rate / median-duration tiles (the gate runs "
                       "behind them, linking to GitHub Actions), the Person page's weekly-activity "
                       "cells (each opens that week's commits in that repo), and the Person page's "
                       "top-repositories / by-element / work-type breakdowns. People counts (active people, and the "
                       "by-company / by-element People columns) open a list of the contributors "
                       "behind them, each linking to their Person page. Traffic, all-time "
                       "surviving-LOC, and pure rates/medians with no window list stay non-clickable."},
            {"type": "feature", "title": "Drill-down rows now show the title",
             "detail": "The commit subject / PR title / issue title is stored at collection "
                       "time and shown as a column in the drill-down modal, so you can tell what "
                       "each row is without opening every link. Titles populate after the next "
                       "report refresh; older rows show “—” until then."},
            {"type": "fix", "title": "Hover tooltips display again",
             "detail": "The floating tooltip (and the back-to-top button) had ended up inside a "
                       "collapsed tab section; a position:fixed element inside a hidden ancestor "
                       "renders as a zero-size box, so hovering a number showed nothing. They’re "
                       "now anchored at the page root and work on every tab."},
            {"type": "fix", "title": "The slice now drives the trend charts",
             "detail": "Both “Activity by week” and the Trend tab’s “Contribution trend — "
                       "monthly by company” are now computed live from the granular tables, so "
                       "they follow the org/element/repo slice and the selected period like the "
                       "other windowed panels — previously they were always org-wide. The Trend "
                       "tab replaces the old snapshot-based chart (which couldn’t be sliced) with "
                       "a monthly commits/LOC-by-company series derived from the commit history."},
            {"type": "feature", "title": "MCP server — query the data from an AI client",
             "detail": "A read-only MCP server exposes the contribution/delivery data to MCP "
                       "clients (e.g. Claude) over HTTPS at insight.example.com/mcp. Tools: "
                       "sql_query (SELECT-only), contribution, delivery, person, taxonomy and "
                       "more — all accept a period and an org/element/repo slice. Manage the "
                       "bearer token under Manage -> MCP."},
            {"type": "feature", "title": "Slice the whole report by org / element / repo",
             "detail": "A second filter next to the period scopes every windowed panel — KPIs, "
                       "by-company, categories, work-type, Delivery — to an organization, product "
                       "element or repository. Period and slice compose. All-time panels (Trend, "
                       "Contributors, Surviving-LOC) stay org-wide."},
            {"type": "feature", "title": "Delivery tab — issue lifecycle & CI health",
             "detail": "A new Delivery tab with period-filtered tiles: issues opened, close "
                       "rate, defect rate, median time-to-close, CI pass rate and duration, "
                       "plus an issue mix by category. Categories/stages/CI roles are resolved "
                       "from the configurable Taxonomy per issue's element/repo, so the same "
                       "label can count differently across teams. Refine the mapping in "
                       "Taxonomy to shrink 'uncategorized'."},
            {"type": "feature", "title": "GitHub profile as an identity hint",
             "detail": "Collect now pulls each person's GitHub profile (name, company, bio, "
                       "location). It shows on their Person page, and in Identity resolution it "
                       "appears as a 'GitHub: …' hint with a one-click 'use' next to name and "
                       "company — a suggestion to help close 'Other'/unresolved, not the "
                       "authoritative value (that stays your override)."},
            {"type": "feature", "title": "Data health tiles are now actionable",
             "detail": "The 'review before sharing' tiles link to where you fix each gap — "
                       "unresolved identities open the Identity editor, unclassified repos open "
                       "Config. Tiles with no in-app fix (traffic access) say what to do on "
                       "GitHub instead of dangling."},
            {"type": "feature", "title": "What's new page",
             "detail": "This page. We'll note notable changes here as they ship."},
            {"type": "security", "title": "Optional portal login",
             "detail": "Set PORTAL_PASSWORD to require a login on every page, export, and "
                       "API. Left unset it stays open for localhost or a reverse proxy that "
                       "adds its own auth; a portal exposed to the network without either now "
                       "prints a loud startup warning."},
            {"type": "security", "title": "Tokens no longer linger in cloned repos",
             "detail": "The GitHub token is stripped from every cloned repo's remote URL, so "
                       "it can't leak through a checkout's .git/config."},
            {"type": "security", "title": "One email, one person — enforced on save",
             "detail": "Saving identities now refuses a roster that assigns the same email to "
                       "two people, which would otherwise split one human's contributions "
                       "across two rows on the next Collect."},
            {"type": "fix", "title": "Traffic no longer drops to zero",
             "detail": "When a refresh couldn't read one repo's traffic, stored clones/views "
                       "were being overwritten with zero. Existing numbers are now preserved "
                       "when a fetch fails."},
            {"type": "fix", "title": "Review coverage can't exceed 100%",
             "detail": "Windowed review coverage compared reviewed PRs against a mismatched "
                       "population and could read above 100% (e.g. 300% for a week). It now "
                       "measures reviewed-in-window against PRs opened in the same window."},
            {"type": "fix", "title": "Issue counts and medians corrected",
             "detail": "Migration-imported issues are excluded from the per-person issue "
                       "counts, and the 'typical' values now use true medians instead of an "
                       "approximation."},
            {"type": "fix", "title": "Two tabs no longer overwrite each other",
             "detail": "If you have the identity or config editor open in two tabs, saving the "
                       "stale one is now rejected with a 'reload before saving' message instead "
                       "of silently clobbering the other tab's edits."},
            {"type": "fix", "title": "Deleted emails stay deleted",
             "detail": "Removing an email in the identity editor used to reappear on the next "
                       "refresh. A curated email list is now authoritative."},
            {"type": "fix", "title": "Honest save messages & fewer lock errors",
             "detail": "The identity Save note now says plainly what applies instantly vs. on "
                       "the next Collect, and a portal save no longer collides with a running "
                       "collect ('database is locked')."},
            {"type": "feature", "title": "Metrics catalog",
             "detail": "A new Metrics screen lists every number the report shows — what it "
                       "means, its formula, and where it's computed. Generated from the code, "
                       "so it can't drift from the implementation."},
            {"type": "feature", "title": "Manage the GitHub token from the UI",
             "detail": "Change, add, or clear the GitHub token from the portal — no editing "
                       "files on the server."},
            {"type": "feature", "title": "Click a person to open their Person tab",
             "detail": "Clicking a name anywhere in the report now opens that person's tab in "
                       "the report instead of jumping out to GitHub."},
            {"type": "redesign", "title": "Update workspace v2",
             "detail": "The Update page was reworked around the DB-first, self-serve tooling — "
                       "connect an org, paste a token, and go."},
            {"type": "redesign", "title": "Clearer sidebar",
             "detail": "The sidebar is grouped into Report and Manage with self-explanatory "
                       "names (Traffic, Repositories, AI tools, Full report, Setup)."},
            {"type": "redesign", "title": "Cleaner period selector",
             "detail": "Segmented presets with the custom range tucked away, and the active "
                       "period — including custom dates — is always visible."},
        ],
    },
]

_PILL = {
    "security": ("Security", "sec"),
    "fix": ("Fix", "fix"),
    "feature": ("New", "feat"),
    "redesign": ("Design", "des"),
}


def releases() -> list:
    """The changelog data as a plain structure (list of {date, changes}), for
    the /api/whats-new JSON endpoint and any other non-HTML consumer. Returns
    CHANGELOG itself — callers must not mutate it."""
    return CHANGELOG



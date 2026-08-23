# Open ledger: ops-site
Opened 2026-08-23. Every item Lazar has not yet answered, plus everything approved and not yet
started. Nothing here is closed until he says so or the work ships.

RULE FOR THIS FILE: an item leaves only two ways. He decides it, or it ships. When one resolves,
its DECIDED line is written under it with the date and the answer. Never delete a row.

HOW TO REFER TO AN ITEM: use its KEY line. Keys are stable and never renumber. A numbered list
inside a chat reply is formatting for that reply and means nothing the next day, so point Lazar
at the key, never at "item 3.2".

The same items are on the In basket tab at https://ops.lazarlalone.com/dashboard.html, and Lazar
answers them in the Ops base in Airtable. `open_items_sync.py` writes his answers back here.

---

## A. WAITING ON LAZAR


OPEN 2026-08-23. **The dashboard rebuilds once a day at 06:17 UTC, so an item you answer **
    The dashboard rebuilds once a day at 06:17 UTC, so an item you answer in Airtable can sit on the page for most of a day. One line makes it hourly. In .github/workflows/refresh.yml change
        - cron: "17 6 * * *"
    to
        - cron: "17 * * * *"
    It costs about 30 free runner minutes a month. Say yes and I will make the change.
    KEY: ops|A|hourly-refresh-instead-of-daily
    Kind: approval. Source: Terminal B 2026-08-23.

OPEN 2026-08-23. **Open https://ops.lazarlalone.com/dashboard.html on your phone and tell**
    Open https://ops.lazarlalone.com/dashboard.html on your phone and tell me what looks wrong. I checked it at desktop width and it reads well, but my browser tool reported a resize to phone width and then kept rendering at desktop width, so I could not actually see the phone layout. The page now carries a viewport tag it never had and every wide table scrolls inside its own box, so it should be fine; I have not seen it.
    KEY: ops|A|eyeball-the-dashboard-on-your-phone
    Kind: your own task. Source: Terminal B 2026-08-23; the check that did not run.

OPEN 2026-08-23. **The Ops tab is only as fresh as the last collector run, and today the **
    The Ops tab is only as fresh as the last collector run, and today the collector runs when someone types the command. Two ways to make it automatic:
    a) A scheduled job on the Mac, next to the two you already have. It reads GitHub with your gh login and the two local logs, so it needs no new keys. It only runs when the Mac is awake.
    b) A step inside the daily refresh workflow. That runs in the cloud every morning whether the Mac is on or not, but it cannot see the two local logs and it needs a GitHub read token.
    My recommendation is both: (a) for the local jobs, (b) for the GitHub ones. I did not add either, because you told me not to change any schedule without asking.
    KEY: ops|A|where-should-the-run-collector-run
    Kind: decision. Source: Terminal B 2026-08-23.

OPEN 2026-08-23. **Test row from the visibility terminal. Safe to drop.**
    Test row from the visibility terminal. Safe to drop.
    KEY: ops-dashboard-refresh|A|test-row-from-the-visibility-terminal-s
    DECIDED 2026-08-23 (dropped): Self-test of open_item.py add/get/list. Dropped, not deleted.

    Kind: info. Source: open_item.py self-test 2026-08-23.

OPEN 2026-08-23. **Process_Runs now holds 456 runs and grows about 750 rows a month, almo**
    Process_Runs now holds 456 runs and grows about 750 rows a month, almost all of it the hourly watchdog. Nothing breaks soon, but the dashboard reads the whole table on every build. Decide a keep-window: 30 days, 90 days, or keep everything and revisit at 10,000 rows. My recommendation is 90 days, which still shows a month of history on the tab.
    KEY: ops|A|how-long-to-keep-run-rows
    Kind: decision. Source: Terminal B 2026-08-23.

OPEN 2026-08-23. **The repo that hosts your dashboard is public and the page is committed**
    The repo that hosts your dashboard is public and the page is committed into it, so every word on it is world-readable and stays in the history. Five of the twenty-five In basket rows name a credential or a file path, so their words are held back on the page and read only in Airtable.
    Three ways to go, pick one:
    a) Leave it. The gate holds those five back and you read them in Airtable. Costs nothing.
    b) Make the repo private. GitHub Pages then needs a paid plan for the site to stay up, so the dashboard would move somewhere else. I have not priced that.
    c) Split: keep the public page for Cost and Ops, and generate the full In basket as a private page you open from your phone.
    My recommendation is (a) for now and (c) later if the held-back rows start mattering.
    KEY: ops|A|the-ops-repo-is-public-decide-what-the-page-may-say
    Kind: decision. Source: Terminal B, from a row-by-row read of all 25 rows 2026-08-23.

OPEN 2026-08-23. **The Misses tab cannot render: the dashboard's Airtable token reaches t**
    The Misses tab cannot render: the dashboard's Airtable token reaches the Ops base only, and the posting calendars and the no-show watchdog live in two other bases. Verified 2026-08-23: all five reads return 403.
    1. Open https://airtable.com/create/tokens
    2. Sign in as lazarlalone@gmail.com (inferred: that account owns the Ops base).
    3. Pick the personal access token whose value is the ops repo secret AIRTABLE_API_KEY, the one that builds https://ops.lazarlalone.com/dashboard.html.
    4. Under Access, Add a base twice: Lazarvision Posts and Fanish Posts. Save changes.
    5. Then tell your terminal, and it adds the two base ids to the ops repo as a POSTS_BASES secret and re-runs the refresh. It worked when the Misses tab shows a 14-day strip per brand.
    KEY: ops|A|widen-the-airtable-token-to-the-posts-bases
    Kind: 30-second fix. Source: Terminal B, verified against the live bases 2026-08-23.

OPEN 2026-08-23. **Four Spend_Variable rows from Aug 16 are duplicates of the same batch,**
    Four Spend_Variable rows from Aug 16 are duplicates of the same batch, from the re-collect bug that fires the recorder again on every collect. They inflate that day by about $0.83. They belong to another terminal's records so I did not delete them. Their ids are recDxYj9lhYPE8b4k, recoDqWOABwjhGps4, recXATQEcMQWMhOHm and recgq92bYqTMe82A9. Delete them when you are ready, or say so and I will.
    KEY: ops|A|four-duplicate-spend-rows-from-aug-16
    Kind: your own task. Source: Prompt B 2.2.4; ids re-read 2026-08-23.

OPEN 2026-08-23. **The Spend_Fixed table in the Ops base is empty, so the dashboard can o**
    The Spend_Fixed table in the Ops base is empty, so the dashboard can only ever show metered variable spend. Add the fixed subscriptions you pay monthly, starting with the $200 Anthropic Max plan, and the Cost tab can start showing what the whole operation costs rather than only the metered part.
    KEY: ops|A|fill-spend-fixed
    Kind: your own task. Source: Prompt B 4.1.5; confirmed empty 2026-08-23.


## B. WAITING ON SOMEONE ELSE

(nothing yet)


## C. KNOWN AND NOT YET SCHEDULED

(nothing yet)

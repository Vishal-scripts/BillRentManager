# Earlier project

The running app is the local program in `ownerapp/`. This note is only about the Google Sheet and WhatsApp design that was tried first and then dropped.

The zip in the handoff was a Google Sheet, one Apps Script file, Meta template text, and an optional FastAPI service meant for Render. The `.env` was left out on purpose. This is what was worth keeping.

## What already worked

- The sheet is the right UI for this owner. Columns A–H match the paper list: S.NO, NAME, ADDRESS, PH. NO, RENT, DATE, STATUS, CA.NO.
- Apps Script can send an approved WhatsApp template with the Cloud API, add `91` to a 10-digit mobile, and read a due day out of `18TH` or `1ST`.
- A morning trigger, a menu, and an **AutomationLog** tab are enough. No login and no database.
- Skipping a row whose STATUS starts with “Paid” is the right idea.
- The bill lookup was already isolated behind a key, with a lock and a pause between CA numbers.

## What was fragile or wrong

- The owner summary template was only `{{1}}`. Meta rejects variable-only bodies. The new templates have fixed Hindi and English around each variable.
- STATUS never reset. One “Paid” silenced that tenant every following month.
- Reminders went out only on the due day, not before it.
- A second click, or a trigger plus the menu, sent the same WhatsApp again. That spends the only thing that costs money. Sends are now keyed on **SentLog**.
- There was no test mode. The first successful run would have messaged tenants.
- A blank NAME dropped the whole row, so plots and the school never reached a bill check. They stay in the sheet now and are skipped only for messages.
- `getTenantSheet` fell back to whichever tab was first. After a Help tab was inserted, that could be the wrong sheet.
- `toLocaleString('en-IN')` is not dependable in Apps Script. Amounts are grouped in plain code.
- The docs disagreed with the code. `DEPLOYMENT.md` described Gmail, Telegram, and “admins only”. `Code.gs` messaged tenants and did not send email. `SETUP_HINDI.md` was a four-line pointer.
- The Python scraper looked for a maths sum in the HTML and filled a dummy mobile `9999999999` and `rental@example.com`. On the archived form the sum is an image (`Img.aspx`), and the email should be the owner’s.
- The amount parser took the first rupee figure on the page. The new parser looks for “amount payable” and the other labelled amounts, and it refuses an 11-digit CA number.
- It launched Playwright with `navigator.webdriver` hidden. That does not help when the firewall rejects the IP before HTML exists, and it is not carried forward.
- The Render service is a host. Free instances sleep, the first request can take a minute, and Apps Script will time out waiting for a cold Playwright browser. The requirement is no paid hosting. The service was removed.
- No seed data, no due date, no Paid/Unpaid bill column, and no monthly collected-vs-pending total.

## What replaced it

A local app in `ownerapp/` (FastAPI, SQLite, a browser UI) runs on the owner’s Windows PC. Rent months, the bill queue, and the PIN live there. The Tata Power reader is still `billcheck/` on that same PC. See [BILL_CHECK.md](BILL_CHECK.md) for the live 406 result.

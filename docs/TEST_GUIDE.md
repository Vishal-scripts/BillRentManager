# Test guide

Use this on the owner’s computer. Nothing in the practice steps pays a bill or sends a message. There is no Google Sheet and no WhatsApp.

## 0. Run from source on Windows

1. Install Python 3.11 or 3.12 from python.org. Tick **Add python.exe to PATH**.
2. Install Tesseract: `winget install UB-Mannheim.TesseractOCR`. Close and reopen the window so `tesseract` is found.
3. Double-click `ownerapp\Start.bat`. Leave the black window open. It creates `.venv` and installs the libraries. If Tesseract is missing, the window prints the winget command.
4. Open `http://127.0.0.1:8765`. The first page asks for a PIN (4 to 8 digits), your name, the email used for the bill check, and a 10-digit mobile. Tata Power’s form marks the email as required and also has a mobile field for the e-receipt. A bad email or a short mobile is refused. Nothing is paid.
5. On the phone, same Wi-Fi, open the address on the Home page. Android Chrome: menu, Add to Home screen. iPhone Safari: Share, Add to Home Screen. If the phone cannot connect, double-click `Allow-Phone-Access.bat` once and press Yes.
6. The captcha is tried **6** times per CA.

macOS: double-click `ownerapp/start.command` after `brew install python@3.12 tesseract`. Linux: `sh ownerapp/start.sh` after `sudo apt install python3 python3-venv tesseract-ocr`.

Practice, with no Tata Power connection:

```bat
cd ownerapp
.venv\Scripts\python.exe -m ownerapp --demo
```

## 1. Practice mode (no Tata Power)

```bat
cd ownerapp
.venv\Scripts\python.exe -m ownerapp --demo
```

The window prints `http://127.0.0.1:8765` and a LAN address. Open the first one. Sign in with the PIN.

Expected: a yellow **Practice data** label. Home shows fictional people (Asha, Bela, Chetan, Diya, Eshan), not the real tenant list.

| Check | Expected |
|---|---|
| Wrong PIN | Stays on the PIN screen |
| Home | At least one rent due today, some pending, one unpaid bill, one bill error |
| Rent | Green paid row, orange due today or within 2 days, red overdue |
| Mark Diya or Asha paid with amount, date, Cash | That row turns green |
| Mark the same row Unpaid | The row is no longer green |
| Tenants search | Typing a name hides the others |
| Add a tenant, then delete it | The row appears, then it is gone |
| Import `data\sample_tenants.csv` | Added or updated rows. The file has CA 60000000001, 60000000002, 60000000003 and the word SAROJ is not in it |
| Excel | A `.xlsx` downloads. Paid rows are green, due rows orange, overdue or error rows red |

### Refresh options in practice mode

On **Bills**:

| Button | Expected |
|---|---|
| All | Progress runs through every CA. Summary `ok=4 paid=3 unpaid=1 errors=1` for the five practice rows |
| Unpaid | Only the unpaid CA. Summary `ok=1 paid=0 unpaid=1 errors=0` |
| Errors | Only the error CA. Summary `ok=0 paid=0 unpaid=0 errors=1` |
| Tick two rows, then Selected | Only those CAs. A repeat tick is queued once |
| One CA `60000000002` | One paid result. Summary `ok=1 paid=1 unpaid=0 errors=0` |
| One CA `123` | Message: type an 11-digit CA. Nothing is queued |

While a run is going, the live panel lists each CA as Waiting, Opening site, Solving captcha (try n of 6), Reading bill, Done, or Error. It shows the current name and CA, the time so far, and a rough time left. **Stop** ends the queue after the current CA. **Retry this** and **Retry all errors** queue the failed ones again. **Run log** and **Run history** open the lines for that run. Each note says `Practice data` and `Not read from Tata Power`. One error does not stop the others. Closing the window and starting again puts a half-finished Checking row back to Queued. Dates are shown in IST, for example `16 Oct 2026, 1:57 PM`. Empty amount lines stay hidden.

`python billcheck\check_bills.py --demo` still prints the saved bill page (CA 60099990002, outstanding 0, exact bill 1310.00, bill number 010214833626). That command does not use the app queue.

## 2. Real bills

1. Start with `ownerapp\Start.bat` (no `--demo`).
2. If the email or mobile is missing, **Bills** shows a banner: **Enter email used for bill check** and the mobile. Save it. The refresh does not start, and no CA is marked Error, until both are valid. Settings has the same two fields. Captcha tries **6**. Seconds between CAs at least 2.
3. **Tenants → Template** downloads a sample Excel file. **Import** shows a preview. A CA that is not 11 digits, a phone that is not 10 digits, a rent that is not a number, or a due day outside 1–31 is rejected and listed. Choose **Update existing** or **Skip existing**, then save. **Excel** downloads tenants, rent, and bills with IST dates.
4. **Bills → Check one CA number** with one real 11-digit number, or **Refresh** on that tenant’s card.

Expected for a success: CHECK **Done**, status Paid or Unpaid, outstanding amount, due date, exact bill, bill number, bill date, name on the bill, last-checked time. Last payment and last paid-on appear only when the history page gives both. If it does not, those stay blank and the bill is still Done.

Expected for a failure: CHECK **Error**, a note, previous amounts left as they were. The next CA still runs. Files land in `billcheck\debug\` when the page could not be read. A JSON line is appended to `data\logs\bills.jsonl`.

| Menu | Who is checked |
|---|---|
| All | Every active tenant with an 11-digit CA, one after another |
| Unpaid | Status is exactly Unpaid |
| Errors | Status starts with Error, or the check state is Error |
| Selected | The CAs whose boxes are ticked |
| One CA | That 11-digit number, if a tenant has it |

A second click while those CAs are already Queued or Checking says they were skipped. The app does not run two scrapes at once.

## 3. Phone on the same Wi-Fi

1. On the Home page, copy the phone address, for example `http://192.168.29.171:8765`.
2. On Windows, allow the port if the phone cannot connect:

```bat
netsh advfirewall firewall add rule name="Narela rent 8765" dir=in action=allow protocol=TCP localport=8765
```

3. The phone must be on the same Wi-Fi, not mobile data.
4. Open that address in the phone’s browser. Enter the PIN.
5. Android Chrome: menu → **Add to Home screen** or **Install app**. iPhone Safari: tap the **Share** button, then **Add to Home Screen**. Safari does not show Chrome’s install prompt. The icon is named Bill Rent Manager.
6. Open **Home**, **Tenants**, **Rent**, and **Bills**. The layout is a single column with the buttons along the bottom.
7. Tap **Alerts on this phone** and allow notifications. Leave the page open and mark a rent row or finish a practice refresh. A notification can appear while the page is open.
8. Turn the phone’s Wi-Fi off, or leave the house. Expected: no alert arrives out there. Come home, open the app again. The alert list shows what changed.

Plain `http://` on a LAN IP is not a secure context, so Chrome may offer a shortcut instead of a full offline install, and it may refuse notifications until the page is open. The list inside the app is the reliable record. There is no cloud push service.

## 4. Month rollover and the weekly bill check

- Rent marked paid in October is still paid when you open October after the 1st of November.
- A tenant with no October mark becomes unpaid history when November starts. The app must have been opened at least once in the earlier month (the first launch does not invent a year of unpaid rows).
- **Settings**: tick weekly auto-refresh, weekday Monday, time 09:00. Expected: the next Monday at or after 09:00, while Start-App is open and the PC is on, every CA is queued once that day. If the window is closed, it waits until the next time the app is open on a later Monday.

## 5. Handover ZIP

```bat
python scripts\build_handover_zip.py
```

Expected: `dist\narela-rent-handover.zip` with the app, the bat files, the docs, and `data/sample_tenants.csv`. It must not contain `.env`, `owner.db`, or `data/tenants.csv`.

## 6. Checklist

1. Double-click `ownerapp\Start.bat`. Leave the black window open.
2. Browser: `http://127.0.0.1:8765`. Sign in with username `admin` and the PIN. The first time, also enter your name, the bill-check email, and a 10-digit mobile.
3. Home shows the phone address, for example `http://192.168.29.171:8765`.
4. Tenants: download the template, import your file, read the preview, then save. A CA that is not 11 digits is rejected.
5. Rent: mark paid with an amount and a date. That line appears in payment history.
6. Bills: if email or mobile is empty, a banner appears and the run does not start. After saving, refresh one CA. The live panel shows Opening site, Solving captcha (try n/6), Reading bill, then Done or Error.
7. An error shows a plain English note and Retry this. Stop leaves the rest of the queue unchecked.
8. Times use India time, for example `16 Oct 2026, 1:57 PM`.
9. Phone, same Wi-Fi: Android Add to Home screen. iPhone Safari Share, then Add to Home Screen.
10. Settings (admin): download a backup and save the file. To add a person, edit `users.env` in Notepad, save, and restart. There is no Users page.

This does not pay a bill. It does not click Pay, Net Banking, SBI Unipay, or BillDesk.

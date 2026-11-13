# Electricity bill check

Each **CA number** is an 11-digit Tata Power-DDL (NDPL) consumer account. The local app on the owner’s PC reads the public pay-bill page and stores the bill in SQLite. There is no Google Sheet and no WhatsApp. Outstanding amount **0** means Paid. **Exact bill** is the bill total and can be non-zero even when nothing is outstanding. The screen also shows due date, bill number, bill date, the name on the bill, last-checked time, and last payment when the history page gives both a date and an amount. An Error leaves the previous amounts, due date, bill number, exact bill, and last payment in place.

The captcha is tried **6** times per CA (`NDPL_CAPTCHA_TRIES`, default 6, allowed 1–12). It is not 3 tries.

## Refresh queue

The **Bills** page queues the work. A background thread on the same PC does the HTTP calls, because Tata Power blocks cloud IPs.

1. **All**, **Unpaid**, **Errors**, **Selected**, or **One CA** adds rows to the queue.
2. The thread claims them, sets **Checking**, and holds a lock so a second run does not start.
3. It checks one CA, waits at least 2 seconds (default 3), writes the result, and moves to the next. One failure does not stop the run.
4. The sticky panel shows Queued, Checking, Done, or Error, a progress bar, the current CA, and `Captcha try 2 of 6`. The line at the end is `Run summary: ok=… paid=… unpaid=… errors=…`.

| Button | Who is queued |
|---|---|
| All | Every active tenant with an 11-digit CA |
| Unpaid | Status is exactly `Unpaid` |
| Errors | Status starts with `Error`, or the check state is `Error` |
| Selected | The ticked rows |
| One CA | That 11-digit CA, if a tenant has it |

A CA already Queued or Checking is skipped. If the app is closed mid-run, the next start puts Checking rows back to Queued. A heartbeat older than 4 minutes is treated the same way.

Practice mode, with no Tata Power connection:

```bat
ownerapp\.venv\Scripts\python.exe -m ownerapp --demo
```

`python billcheck/check_bills.py --demo` still parses the saved HTML fixtures and does not use the queue.

Last payment: after a Paid or Unpaid bill page, the checker GETs a same-origin `/billpay/` address whose path or window title contains “history”, and only when that address is not a payment host (BillDesk, PayU, Razorpay, CCAvenue, Unipay, SBI ePay). **LAST PAYMENT** and **LAST PAID ON** are written only when both parse. A missing or unclear history page leaves those cells blank and does not mark the bill Error. It never clicks a pay option.

## What was tried on 10 Oct 2026

From this network, a normal browser-style request to the live site did not return the form.

| URL | Result |
|---|---|
| `https://www.tatapower-ddl.com/billpay/paybillonline.aspx` | HTTP **406** from `184.72.220.21` and again from `34.232.125.200` |
| `https://www.tatapower-ddl.com/billpay/paybill.aspx` | HTTP 406 |
| `https://www.tatapower-ddl.com/billpay/Img.aspx` | HTTP 406 |
| `https://tatapower-ddl.com/billpay/paybillonline.aspx` | HTTP 406 |
| A second fetcher (not curl) | HTTP 406 |
| `wss.tatapower-ddl.com`, `api.tatapower-ddl.com`, `consumer.tatapower-ddl.com` | No such host |

The 406 page is AppTrana’s block page: “Request was blocked due to suspicious behavior.” A copy is in [probes/ndpl-406.html](probes/ndpl-406.html). The same class of cloud IP is what GitHub Actions and a Render container use, so a scheduled cloud job is not a reliable design. `.github/workflows/bill-check.yml` is a **manual** probe only. It has no schedule. Expect it to print `blocked=True`.

There is no free public JSON API for this. Bharat Connect / BBPS bill-fetch APIs are paid services and are not used.

The form itself is known from the Internet Archive snapshot of `paybillonline.aspx` on **31 Oct 2023** (`web.archive.org`, timestamp `20231031111312`), which is still the page search engines describe:

- `txtcano` — CA number
- `txtmobile` — mobile for the e-receipt (optional on the form)
- `txtemail` — email for the e-receipt (marked required)
- `Img.aspx` — captcha **image**
- `TxtImgVer` — placeholder text “Enter addition of two numbers”
- `btnpay` — button label **Pay Now**

The two numbers are drawn in the image, in dark green, with a line struck through them. They are not in the HTML, so a text-only scraper cannot solve them. The checker OCRs that image locally with Tesseract (free) and submits the sum only when several readings agree. It tries **6** images per CA, not 3 (`NDPL_CAPTCHA_TRIES`, default 6). A doubtful image is refreshed with Change Image, or the page is reloaded, instead of posting a guess. CAs that still fail are marked **Error** and tried once more after the rest of the list. The audio button on the form is not used: reading it would need a speech engine, and the image path is the one that is tested.

Each captcha image is enlarged, turned to grey and also to a green-digit mask, thresholded, and the horizontal strike is removed when it is clearly longer than the digit strokes. Tesseract then reads it with a digits-and-plus whitelist in more than one page mode. The sum is posted only when one reading leads every other reading by at least two votes, and at least two reads agree. A single guess, a tie, or a one-vote lead is left unread and the checker refreshes the image. On the sample set in `tests/test_captcha.py` (DejaVu, dark green or black, strike through the middle, Tesseract 5.3) that accepted **41 of 52** images (**78.8%**). None of the accepted answers were wrong. The other 11 were left unread. The test requires zero wrong answers. `billcheck/fixtures/captcha_16_plus_22.png` is drawn in that same style: dark green **16 + 22** with a line through the digits, which is the captcha on the pay-bill form. It solves to **38**.

The button is labelled Pay Now because that is the site’s own first step. The script posts the CA number, the owner’s email, and the captcha sum. It does **not** open a card page, and it has no card number to send. It does not click **Net Banking**, **SBI Unipay**, **BillDesk**, or the amount box. If the response is already a payment gateway (a card or CVV page, with no bill table) that CA is stored as **Error** and the run moves on.

After a correct captcha the site shows a **BILL DETAILS** table. The checker reads CA number, name, bill number, bill date, due date, exact bill, and outstanding amount. Labels match if the letters are the same regardless of case or spaces. Outstanding **0** is **Paid**. Outstanding above 0 is **Unpaid**, and that outstanding figure is the amount due. The CA printed on the page must be the CA that was requested, and the confirmation line must name the same CA. If any of those fields cannot be read exactly, the row is **Error** and no amount is written. A result that arrives as a new tab is read with a normal GET when the address is still on tatapower-ddl.com. A payment-company address is not opened.

`billcheck/fixtures/result_bill_details.html` follows the owner’s screenshot of that table (CA 60099990002, Ms. ASHA ., bill 010000000001 dated 1-Oct-2026, due 16-Oct-2026, exact bill Rs. 1310.00, outstanding Rs. ₹ 0). It was not downloaded from the live site: this network still receives HTTP 406. The other result fixtures are synthetic stand-ins for older wording. If a CA comes back as “Could not read”, the page wording has changed or a value was not exact. The HTML and a text snapshot are saved under `billcheck/debug/`, and the note on the Bills page names those files.

One CA failing does not stop the run. A timeout, a network error, a maintenance page, a rejected CA, a rejected captcha, or a page that cannot be parsed is written as **Error** for that CA and the next CA is checked. If the connection drops, the session is opened again and that CA is tried once more. If Tesseract is missing, the run stops before the first CA, because every CA would fail the same way. If Tata Power returns HTTP 406, that CA is **Error** and the remaining CAs are marked with the same note without calling the site again. At the end the app shows `Run summary: ok=… paid=… unpaid=… errors=…` and appends a JSON line per event to `data/logs/bills.jsonl`.

## Why it runs on the owner’s PC

A data-centre address is what this firewall already refuses. The owner’s home connection in Delhi is the one the site expects. The app waits at least 2 seconds between CA numbers (default **3**). A full list is a few minutes, not a burst.

Weekly is enough. Electricity bills do not change every day. Settings can queue every CA on Monday at 09:00 while `Start-App.bat` is open.

## Install on Windows

1. Install Python 3.11 or 3.12 from [python.org](https://www.python.org/downloads/windows/). Tick **Add python.exe to PATH**.
2. Install Tesseract: `winget install UB-Mannheim.TesseractOCR`. Close and reopen the terminal so `tesseract` is on the PATH. The UB Mannheim build is free. A machine-wide install is `C:\Program Files\Tesseract-OCR\tesseract.exe`. A per-user winget install is `%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe`. The app checks both, and Program Files (x86). If `tesseract` is still not found, set this in `billcheck\.env`:

```text
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```
3. Double-click `ownerapp\Setup.bat`. It writes `ownerapp\.env` with `RECEIPT_EMAIL`, `RECEIPT_MOBILE`, `REQUEST_DELAY_SECONDS=3`, and `NDPL_CAPTCHA_TRIES=6`. The PIN is chosen in the browser and stored as a hash in `users.env`. If `tesseract` is not on PATH, Setup also writes `TESSERACT_CMD`.
4. `RECEIPT_EMAIL` is required by Tata Power’s form. Use the owner’s email, not a tenant’s. Nothing is paid.
5. Double-click `ownerapp\Start-App.bat` and leave the window open. Open `http://127.0.0.1:8765`. One session is kept for the CAs in a batch.
6. When a batch finishes, each row shows Done or Error and the summary line is on the Bills page.

## Login task

Setup can register task `BillRentManager`, or run `ownerapp\Install-Login-Task.bat`. It starts `Start-App.bat` at logon. If `schtasks` fails, double-click `Start-App.bat` yourself. The PC must be on. The weekly bill refresh is a switch in **Settings**, not a second scheduled scrape.

## If a CA fails

| BILL STATUS | Meaning |
|---|---|
| Unpaid | Outstanding amount is greater than zero. That amount is BILL AMOUNT |
| Paid | Outstanding amount is zero, or an older page says there are no dues |
| Error: Captcha could not be solved after 6 tries… | The image was not confident, or the site rejected the sum, on every try. Use **Errors** later |
| Error: CA number was rejected | The 11 digits were not accepted |
| Error: CA on the result page does not match | The page showed a different CA. Nothing was written for this row |
| Error: Could not read: … | A bill field was missing or not exact. See `billcheck/debug/` |
| Error: blocked | This computer was given HTTP 406. The run finishes the list without calling the site again. Run it later from the home PC |

The app keeps the previous amount, due date, bill number, and exact bill when the new status is Error.

`python check_bills.py --demo` parses the saved fixtures and does not touch the network. `python check_bills.py --probe` fetches the form once and reports the HTTP status.

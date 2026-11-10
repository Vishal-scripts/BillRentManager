# Bill Rent Manager — handover

This program runs on the owner's computer. It keeps tenants, monthly rent, and Tata Power-DDL electricity bills in a file on that computer. It does not use Google Sheets, WhatsApp, or SMS. It does not pay a bill.

A day-to-day page list is in [OWNER_GUIDE.md](OWNER_GUIDE.md). How the bill page is read is in [BILL_CHECK.md](BILL_CHECK.md).

## What you need

- Windows 10 or 11, a Mac, or Linux.
- For a source install: Python 3.11 or 3.12, and Tesseract with the English language data.
- The Windows zip already contains Python and Tesseract. The owner does not install them.

The Windows zip is built on a Linux machine with `scripts/build_windows_portable.py`. That build downloads Python and Tesseract and compiles `BillRentManager.exe`. It still needs a Windows PC to confirm the double-click, the firewall prompt, and a phone on the house Wi-Fi. This repository's Linux checks do not replace that.

## Run it

### Windows zip

1. Unzip `BillRentManager-windows-x64.zip`.
2. Open the `BillRentManager` folder.
3. Double-click `BillRentManager.exe`. Leave the black window open. Closing it stops the app.
4. If Windows says the program is protected, choose **More info**, then **Run anyway**.

The browser opens at `http://127.0.0.1:8765`.

### From source

```bash
python3 -m pip install -r requirements.txt
python3 -m ownerapp
```

On Windows, double-click `ownerapp\Start.bat` instead. It creates a virtual environment and installs the libraries.

Open `http://127.0.0.1:8765`.

Practice data, with no call to Tata Power:

```bash
python3 -m ownerapp --demo
```

On Windows, `Demo Mode.bat` does the same thing inside the zip.

## First start

The browser asks for:

- Your name
- A PIN of 4 to 8 digits
- The email Tata Power's form asks for
- A 10-digit mobile for the e-receipt

Nothing is paid. The username is `admin`. The PIN is stored as a hash in `users.env`, next to the program. The digits are not kept. `0000`, `1234`, `123456`, and a repeated digit are refused.

Do this on the computer that runs the app. A phone cannot create the admin account.

Sign in later with `admin` and that PIN. Five wrong tries lock that username for 5 minutes. The lock remains if the app is restarted.

## Add or remove people

The app does not create accounts in the browser.

1. Open `users.env` in Notepad. From source it sits next to the `ownerapp` folder. In the Windows zip it sits next to `BillRentManager.exe`.
2. One person per line: `username=PIN`. You may type the digits. The next start replaces them with a hash.
3. The first person is an admin. Later lines are staff unless the line ends with `:admin` or `:staff`.
4. Save, then restart the app. At most 10 people.

A staff member can view, refresh bills, mark rent, and add or edit tenants. A staff member cannot open Settings, delete a tenant, or restore a backup.

`pin.pepper` sits next to `users.env`. Keep both files. Without the pepper, the hashes cannot be checked. Do not commit either file.

## Tenants and Excel

On **Tenants**:

- Add a person with name, room, phone, rent, due day (`18` or `18TH`), an 11-digit CA number, and notes.
- Phone, when filled in, must be 10 digits. A blank phone is allowed.
- **Template** downloads a sample Excel file.
- **Import** reads CSV or Excel and shows a preview. A bad CA, phone, rent, or due day is listed and not saved. A formula cell is rejected. Type the value instead.
- **Excel** downloads a workbook. Paid rows are green, due rows orange, overdue rows red. A name that starts with `=` is stored as text, not as a formula.

Rent is one month at a time. Green is paid. Amber is due today or within 2 days. Red is overdue. A past month with no saved payment is "No record", not overdue. A month before the person was added is "Not a tenant yet". Mark paid with an amount greater than zero, a date, and Cash, UPI, Bank, or Other.

## Backup and restore

Settings, on this computer only:

- **Download backup** saves the database. The file does not contain the sign-in cookie secret.
- **Restore** replaces tenants, rent, and bills with that file.

A phone cannot download or restore a backup, even for an admin. Copy the whole program folder, including `data` and `users.env`, when you move to another computer. Do not copy only the EXE.

## Phone on the same Wi-Fi

The app listens only on the computer until phone access is turned on.

1. On the computer, open **Settings** and choose **Turn phone access on**.
2. Restart the app. Home shows an address like `http://192.168.1.20:8765`.
3. On Windows, double-click `Allow-Phone-Access.bat` once and press Yes. That opens ports 8765–8784 and writes `phone-access.on`.
4. The phone must be on the same Wi-Fi, not mobile data.

`python3 -m ownerapp --phone` turns phone access on for that run. Deleting `phone-access.on` and restarting turns it off.

Anyone on the Wi-Fi who knows a username and PIN can open the app while phone access is on. That is why the PIN must not be obvious, and why setup and backups stay on the computer.

### System alerts on the phone

Plain `http://192.168.x.x` is not a secure page. Chrome and Safari will not show system notifications there, and they may not install a full offline app. The alert list inside the app still works. Come back to the Wi-Fi and open the app to see what changed.

To allow system alerts:

```bash
python3 -m ownerapp --https --phone
```

The first time, OpenSSL creates a certificate in `data/certs/`. The phone shows a warning because the certificate is not from a public authority. Accept it for this computer, then open the `https://` address from Home and allow alerts. On the computer, `https://127.0.0.1:8765` works the same way after you accept the warning. Localhost over plain http is already allowed to show alerts.

The Windows zip does not bundle OpenSSL. HTTPS from the zip needs `cert.pem` and `key.pem` placed in `data\certs\`, or OpenSSL installed so the first `--https` start can create them. The double-click launcher uses HTTPS only when `data\certs\cert.pem` is already there.

## Tata Power bills

Each CA number is 11 digits. **Bills** can refresh one CA, the ticked rows, the unpaid rows, the error rows, or every CA. One failure does not stop the next. An error keeps the previous amount.

The check runs on this computer because Tata Power blocks many data-centre addresses. From some networks the site answers HTTP 406 before the form appears. That is an error for the CA, and the rest of that run is marked the same way without calling the site again. Run it later from the house connection.

The captcha is two numbers added together. The app reads the image locally. It posts the sum only when every reading agrees, at least twice. Otherwise it asks for a new image. The default is 6 tries (1 to 12 in Settings). It never clicks Pay, Net Banking, SBI Unipay, or BillDesk.

A page that says there are no dues, or shows an outstanding amount of zero, is stored as Paid. An unpaid page is stored only when the amount, exact bill, due date, and bill number can be read.

Settings can queue every CA once a week while the app is open. If the email, the mobile, or Tesseract is not ready, that week's run waits. It does not burn the day. The same check blocks the Refresh button.

Seconds between CA numbers is the value in Settings, and it is never below 2.

If a check is still marked Checking and its heartbeat is older than 4 minutes, the next pass puts it back to Queued. Closing the app mid-run does the same on the next start.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| The browser asks for a PIN and will not take 0000 | Choose another PIN. 0000 is refused on purpose. |
| "Wrong username or PIN" | Check `users.env`. Restart after you edit it. |
| Locked for 5 minutes | Wait. Restarting does not clear the lock. |
| Phone cannot connect | Phone access on, app restarted, same Wi-Fi, then `Allow-Phone-Access.bat` on Windows. |
| Phone opens the app but no system alert | Use `--https`, or rely on the list inside the app. |
| Every bill says blocked or HTTP 406 | The network is blocked by Tata Power. Try again from the house PC later. |
| "Tesseract was not found" | Install Tesseract, or use the Windows zip, which includes it. |
| A bill stays Error | Read the note. Retry that CA. The previous amount is kept. |
| Backup from the phone is refused | That is intentional. Download the backup on the computer. |

## What was not verified on a Windows PC

The source was exercised on Linux: sign-in, tenants, rent, Excel, the practice bill queue, and the pages in a desktop browser and a narrow window. The live Tata Power form was not read from the network used for that check (HTTP 406). The Windows EXE, the firewall prompt, and a real phone on Wi-Fi still need a Windows test.

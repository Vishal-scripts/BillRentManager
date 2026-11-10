# Bill Rent Manager

Bill Rent Manager keeps tenants, monthly rent, and Tata Power-DDL electricity bills on one computer. Open it in a browser on that computer, or on a phone on the same Wi-Fi.

It does not use a Google Sheet, WhatsApp, Telegram, or SMS. It does not pay a bill. The only network call that leaves the house is the electricity check to Tata Power, and only from this PC.

The day-to-day guide is [docs/OWNER_GUIDE.md](docs/OWNER_GUIDE.md). A handover for the client is [docs/CLIENT_HANDOVER.md](docs/CLIENT_HANDOVER.md). A test checklist is [docs/TEST_GUIDE.md](docs/TEST_GUIDE.md). How the captcha and the bill page are read is [docs/BILL_CHECK.md](docs/BILL_CHECK.md).

## Features

- **Home** shows rent due today, overdue rent, collected and pending amounts, unpaid bills, bills due within 3 days, and bill errors.
- **Tenants** holds name, room, phone, rent, due day, 11-digit CA number, and notes. Search, import CSV or Excel, and export a coloured workbook.
- **Rent** is one month at a time. Green is paid, amber is due soon, red is overdue, grey is unknown. Mark paid with an amount, a date, and Cash, UPI, Bank, or Other.
- **Bills** checks each CA. Refresh one card, the ticked rows, the unpaid rows, or every CA. A live panel shows the step, the captcha try, the time so far, and a rough time left. Stop ends the queue after the current CA. A failed CA keeps the previous amount.
- **Sign-in** is a username plus a PIN. Up to 10 people. The first person is an admin. Staff can view, refresh bills, mark rent, and add or edit tenants. Staff cannot open Settings, delete a tenant, or restore a backup. Five wrong PINs lock that username for 5 minutes.
- **Activity** (admin) records who marked rent, refreshed bills, or edited a tenant.
- **Theme** is Light, Dark, or System. Light is the default. The control is in the top bar and in Settings.
- **Self-check** on startup confirms the Python libraries, a Tesseract test read, a writable data folder, and a free port. If something is missing, the app shows one fix page instead of an error on every CA.
- **Practice mode** (`--demo`) uses fake bill numbers and does not open Tata Power.

## Screenshots

Pictures of the app go in [docs/screenshots/](docs/screenshots/). See [docs/screenshots/README.md](docs/screenshots/README.md) for the suggested set: sign-in, light home, dark home, bills on a phone, and the Settings note about `users.env`.

## Sign-in file (`users.env`)

The app does not create accounts in the browser. You edit a text file.

- From source, the file is `users.env` in this folder (next to `ownerapp/`).
- In the Windows portable folder, it is `BillRentManager\users.env`, next to `BillRentManager.exe`.
- Copy [users.env.example](users.env.example) to `users.env` if the app has not created it yet.

```
# username=PIN   (4-8 digits). Max 10 users. Save the file, then restart the app.
admin=123456
ramesh=4821
priya=9090:admin
```

The PIN is 4 to 8 digits. The first line is an admin. Later lines are staff unless they end with `:admin` or `:staff`. At most 10 people. Extra lines are ignored. A bad line is skipped, and the black window says which line. Delete a line to remove that person, then restart. Keep `users.env` private. It is listed in `.gitignore`.

If the file is missing on first start, the browser asks for a name and a PIN and writes `admin=that PIN`. You can also type the admin PIN in the console when the window asks.

## Run from source

You need Python 3.11 or 3.12 and Tesseract with the English language data. Then:

```bash
python3 -m pip install -r requirements.txt
python3 -m ownerapp
```

Open `http://127.0.0.1:8765`. Leave the terminal open. Close it to stop the app.

By default the app listens only on this computer. Phone access is off until you turn it on in Settings (or create a `phone-access.on` file next to `users.env`) and start again with the same command. `python3 -m ownerapp --phone` does that for one run. `python3 -m ownerapp --https` serves HTTPS with a certificate in `data/certs/` so a phone browser can show system alerts. On plain `http://` to a LAN address, the phone can still open the app, but the browser will not show system alerts.

### Windows

1. Install Python 3.11 or 3.12 from [python.org](https://www.python.org/downloads/windows/). Tick **Add python.exe to PATH**.
2. Double-click `ownerapp\Start.bat` and leave the window open. It creates `.venv`, installs the libraries, and looks for Tesseract. If Tesseract is missing, it runs `winget install UB-Mannheim.TesseractOCR` and then continues.
3. Open `http://127.0.0.1:8765`. The first time, enter your name, a PIN, the bill-check email, and a 10-digit mobile. Your username is `admin`.
4. On a phone on the same Wi-Fi, open the address shown on Home. If it cannot connect, double-click `windows\Allow-Phone-Access.bat` once and press Yes.

### macOS

```bash
brew install python@3.12 tesseract
python3 -m pip install -r requirements.txt
sh ownerapp/start.sh
```

Or double-click `ownerapp/start.command`. Open `http://127.0.0.1:8765`.

### Linux

```bash
sudo apt install python3 python3-venv python3-pip tesseract-ocr
python3 -m pip install -r requirements.txt
sh ownerapp/start.sh
```

Open `http://127.0.0.1:8765`.

Practice mode does not contact Tata Power:

```bash
python3 -m ownerapp --demo
```

The captcha is tried 6 times. The run never clicks Pay, Net Banking, SBI Unipay, or BillDesk.

## Build the portable Windows package

On a Linux machine with `p7zip-full` and `gcc-mingw-w64-x86-64`:

```bash
python3 scripts/build_windows_portable.py
```

That writes `dist/BillRentManager-windows-x64.zip`. Unzip it on Windows and double-click `BillRentManager.exe`. Python and Tesseract are inside the folder. Details are in [docs/PORTABLE.md](docs/PORTABLE.md).

## Tests

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -p 'test_*.py'
```

`data/tenants.csv` is a private list and is not in this archive. The seed test skips when that file is absent. `data/sample_tenants.csv` is fake practice data and is included.

## License

No license has been chosen. [LICENSE](LICENSE) is a placeholder. Ask the owner before you copy or reuse this project.

# Bill Rent Manager

This app runs on your computer (Windows, Mac, or Linux). It keeps tenants, rent, and Tata Power electricity bills on that computer.

It does not use Google Sheets, WhatsApp, Telegram, or SMS. It does not pay a bill. It never clicks Pay, Net Banking, SBI Unipay, or BillDesk.

## Windows: one double-click

Unzip `BillRentManager-windows-x64.zip`. Open the `BillRentManager` folder. Double-click `BillRentManager.exe`.

Leave the black window open. Closing it stops the app. If the app is already running, the next double-click opens the existing window.

The folder already contains Python and Tesseract. You do not install them.

If Windows says "Windows protected your PC", click **More info**, then **Run anyway**. The program is not from the Microsoft Store. If Windows Defender removes a file, choose **Allow** for this folder, then start again.

## First start

The browser asks for your name, a PIN of 4 to 8 digits, the email used for the bill check, and a 10-digit mobile. Tata Power's form requires the email. The mobile is for the e-receipt. Nothing is paid.

That PIN is saved as the user `admin` in `users.env`.

Sign in later with the username and the PIN.

## Add or remove people

The app does not create accounts. Anyone on the Wi-Fi could do that if it did.

1. Open `users.env` in Notepad. On Windows it sits in the `BillRentManager` folder, next to `BillRentManager.exe`. From source code it sits next to the `ownerapp` folder.
2. One person per line: `username=PIN`. The PIN is 4 to 8 digits.
3. The first person is an admin. Later lines are staff unless you add `:admin` or `:staff`.
4. Save the file, then restart the app.

```
# username=PIN   (4-8 digits). Max 10 users. Save the file, then restart the app.
admin=123456
ramesh=4821
priya=9090:admin
```

At most 10 users. Extra lines are ignored. A bad line is skipped. The black window says which line.

Delete a line to remove that person. Their open session stops working.

An **admin** can use Settings, backup, restore, and delete a tenant.

A **staff** member can view, refresh bills, mark rent paid, and add or edit tenants. A staff member cannot open Settings, delete a tenant, or restore a backup.

Five wrong PINs lock that username for 5 minutes.

## Pages

**Home, Tenants, Rent, Bills.** Admins also have **Settings**.

**Tenants:** name, room, phone, rent, due day (`18` or `18TH`), CA number, notes. Search, add, import CSV or Excel, download Excel. Turning Active off hides the person from rent and bills. Delete removes the history. Only an admin can delete.

**Rent colours**

| Colour | Meaning |
| --- | --- |
| Green | Paid |
| Amber | Due today or within 2 days |
| Red | Overdue, or unpaid in an older month |
| Grey | Not known yet |

**Bills:** each card has Refresh. Tick rows and use Refresh selected, or type one 11-digit CA. The panel shows Queued, Checking, captcha try 1 of 6, then Done or Error. One failure does not stop the next CA. A failed check keeps the previous amount.

If email, mobile, Tesseract, or the data folder is not ready, the app shows a fix page. It does not mark each CA as an error for a missing program.

**Settings** (admin): email, mobile, weekly refresh, theme, backup, and the activity list. The theme is Light, Dark, or System. Light is the default. The same choice is in the top bar, so staff can use it too.

Activity records who marked rent, refreshed bills, or edited a tenant.

## Phone

Phone access is off until you turn it on. In Settings, on this computer, choose **Turn phone access on**, then restart the app. Home then shows an address like `http://192.168.1.20:8765`. The phone must be on the same Wi-Fi. Double-click `Allow-Phone-Access.bat` once and press Yes so Windows allows the port.

Android Chrome: menu, Add to Home screen. iPhone Safari: Share, Add to Home Screen.

A phone on plain `http://` can open the pages and the alert list. The browser will not show system alerts on that address, and it will not install a full offline app. Restart with `--https` if you want system alerts, then accept the certificate warning on the phone. Away from this Wi-Fi, the phone cannot reach the computer. This is not WhatsApp.

Setup, backup, and restore work only on the computer that runs the app. A phone cannot do those, even for an admin.

## Practice data

Double-click `Demo Mode.bat`. The screen says **Practice data**. Those bills are not real.

## Copy the app

Copy the whole `BillRentManager` folder. The `data` folder holds the rent and bills. Do not copy the EXE on its own. `users.env` holds the usernames and PINs. Keep that file private.

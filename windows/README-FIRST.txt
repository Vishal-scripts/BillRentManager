Bill Rent Manager
=================

This folder does not pay a bill. It does not click Pay, Net Banking,
SBI Unipay, or BillDesk.

START
-----
1. Double-click BillRentManager.exe.
2. A black window opens. Leave it open. Closing it stops the app.
3. The browser opens on this computer. If it does not, open the address
   printed in the black window (usually http://127.0.0.1:8765).
4. If the app is already running, the next double-click opens that one.

Windows may say "Windows protected your PC". Click More info, then
Run anyway. This program is not from the Microsoft Store. If Windows
Defender removes a file, allow this folder, then start again.

The first page asks for your name, a PIN (4 to 8 digits), the email
used for the bill check, and a 10-digit mobile. Nothing is paid.
Your username is admin. The PIN is saved in users.env.

PHONE ON THE SAME WI-FI
-----------------------
1. Double-click Allow-Phone-Access.bat once. Press Yes.
   That only allows the phone through the firewall. It does not start the app.
2. On the phone, open the address shown on the Home page.

PEOPLE WHO CAN SIGN IN
----------------------
The app does not create accounts.

1. Open users.env in Notepad. It is in this folder.
2. One person per line, for example:
   admin=123456
   ramesh=4821
3. The first line is an admin. Later lines are staff unless the line
   ends with :admin or :staff.
4. At most 10 people. Save the file, then restart the app.
5. Delete a line to remove that person.

An admin can open Settings, take a backup, restore, and delete a tenant.
A staff member can view, refresh bills, mark rent paid, and add or edit
tenants. Five wrong PINs lock that username for 5 minutes.

DEMO
----
Double-click Demo Mode.bat for practice numbers. They are not real bills.
The screen says Practice data. The first start can still ask for a PIN.

YOUR OWN LIST
-------------
Tenants page: Import. Choose your CSV or Excel file.
sample\sample_tenants.csv is for practice. Those are not real people.

BILL CHECK
----------
On Bills, type one real 11-digit CA and press Check this CA.
If the result looks right, press All.
The panel shows Queued, Checking, captcha try 1 of 6, then Done or Error.
If something the program needs is missing, you get one fix page.
This does not pay the bill.

THEME
-----
The top bar has Light, Dark, and System. Light is the default.
Admins can also change it in Settings.

COPY
----
The data folder stores the rent and the bills. Copy this whole folder.
Do not copy the EXE on its own. Keep users.env private.

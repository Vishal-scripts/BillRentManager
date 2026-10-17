# Live probe, 10 Oct 2026

`ndpl-406.html` is the body returned by:

`GET https://www.tatapower-ddl.com/billpay/paybillonline.aspx`

Status **406 Not Acceptable**. The page is served by AppTrana (`custom-page.apptrana.com`) and says the request was blocked. Two source addresses were reported on two attempts: `184.72.220.21` and `34.232.125.200`. A non-curl fetch of the same URL also returned 406.

`paybill.aspx` and `Img.aspx` on the same host returned 406. `wss.tatapower-ddl.com`, `api.tatapower-ddl.com`, and `consumer.tatapower-ddl.com` did not resolve.

No bill amount was retrieved, because the form never loaded. The checker is written against the archived form field names and is meant to be run from the owner’s PC. Details are in [../BILL_CHECK.md](../BILL_CHECK.md).

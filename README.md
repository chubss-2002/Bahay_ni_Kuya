# Staywise Boarding House Management System

Flask + SQLite demo project. Tenant accounts can choose an available room and record a simulated rent payment from the website. **The payment demo does not connect to GCash, Maya, a bank, or any payment gateway, and it never transfers real money.**

## Run on Windows / VS Code

1. Extract the project ZIP.
2. Open the `boarding_house_management_system` folder in VS Code.
3. Open **Terminal → New Terminal**.
4. (Recommended) Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

5. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

6. Start the app:

   ```powershell
   python app.py
   ```

7. Visit `http://127.0.0.1:5000`.

## Demo administrator login

- Email: `admin@example.com`
- Password: `admin123`

## Tenant demo flow

1. Choose **Register** on the login page and create an account (password must be at least 8 characters).
2. Sign in with the tenant account.
3. Open **Rooms** and choose an available room. Room selection checks capacity.
4. Open **Payments**, select a billing month and a simulated method (GCash demo, Maya demo, or cash demo), then click **Pay rent (Demo)**.
5. Review the transaction in payment history. Demo transactions are labeled and use a `DEMO-` reference; no real payment is processed. A second paid record for the same billing month is blocked.

## Important notes

- Back up `boarding_house.db` before replacing project files.
- Change the default admin password and set a strong `SECRET_KEY` before any deployment.
- This is a demonstration workflow, not a production payment system. For real online payments, integrate an approved payment gateway with verified server-side callbacks/webhooks and appropriate security controls.

# ============================================================
# TELEBIRR DEPOSIT RECEIPTS - CORRECTED
# ============================================================

def extract_receipt(receipt):
    """
    Extract amount and transaction reference from TeleBirr receipt.

    Supports common English and Amharic receipt labels.
    Does not treat arbitrary numbers as payment confirmation.
    """

    text = str(receipt or "").strip()

    if len(text) < 10:
        return None, None

    # Normalize spaces while preserving receipt text.
    normalized = re.sub(r"[ \t]+", " ", text)

    # --------------------------------------------------------
    # AMOUNT EXTRACTION
    # --------------------------------------------------------

    amount_patterns = [

        r"(?:amount|total amount|paid amount|transfer amount|"
        r"payment amount|የተላከው\s*መጠን|መጠን)"
        r"\s*[:\-]?\s*(?:ETB|Birr)?\s*"
        r"([0-9,]+(?:\.[0-9]{1,2})?)",

        r"(?:ETB|Birr)\s*"
        r"([0-9,]+(?:\.[0-9]{1,2})?)",

        r"([0-9,]+(?:\.[0-9]{1,2})?)\s*(?:ETB|Birr)"
    ]

    amount = None

    for pattern in amount_patterns:

        match = re.search(
            pattern,
            normalized,
            re.IGNORECASE
        )

        if match:
            try:
                amount = round(
                    float(match.group(1).replace(",", "")),
                    2
                )
                break
            except ValueError:
                continue

    # --------------------------------------------------------
    # TRANSACTION REFERENCE EXTRACTION
    # --------------------------------------------------------

    reference_patterns = [

        r"(?:transaction\s*(?:id|no|number|reference)?|"
        r"txn\s*(?:id|no|number)?|"
        r"reference\s*(?:number|no|id)?|"
        r"receipt\s*(?:number|no|id)?|"
        r"ref\s*(?:number|no|id)?|"
        r"የግብይት\s*ቁጥር|"
        r"የማጣቀሻ\s*ቁጥር)"
        r"\s*[:#\-]?\s*"
        r"([A-Za-z0-9][A-Za-z0-9/\-]{5,})"
    ]

    reference = None

    for pattern in reference_patterns:

        match = re.search(
            pattern,
            normalized,
            re.IGNORECASE
        )

        if match:
            reference = match.group(1).strip().upper()
            break

    # Do not credit an incomplete receipt.
    if not amount or not reference:
        return None, None

    if amount < MIN_STAKE or amount > 1000000:
        return None, None

    return amount, reference


# ============================================================
# PROCESS DEPOSIT
# ============================================================

def process_deposit(user_id, receipt_text):

    user_id = str(user_id)

    receipt_text = clean_text(receipt_text, 4000)

    if not user_id or not receipt_text:
        return False, "Missing user ID or receipt."

    amount, reference = extract_receipt(receipt_text)

    if not amount or not reference:
        return False, (
            "The system could not identify the payment amount "
            "and transaction reference.\n\n"
            "Please copy and submit the complete TeleBirr receipt."
        )

    try:

        with db() as con:

            # Lock the transaction while checking and crediting.
            con.execute("BEGIN IMMEDIATE")

            # ------------------------------------------------
            # CHECK EXISTING TRANSACTION
            # ------------------------------------------------

            existing = con.execute("""
                SELECT *
                FROM deposits
                WHERE transaction_ref=?
            """, (reference,)).fetchone()

            if existing:

                # Same player submitting again after a timeout.
                # Do not credit the wallet a second time.
                if str(existing["user_id"]) == user_id:

                    if existing["status"] in (
                        "PROVISIONAL",
                        "VERIFIED"
                    ):

                        return True, {
                            "amount": float(existing["amount"]),
                            "reference": reference,
                            "status": existing["status"],
                            "already_credited": True
                        }

                    return False, (
                        "This transaction was previously rejected. "
                        "Please contact Guder Bingo support."
                    )

                # Another player already submitted this reference.
                return False, (
                    "This TeleBirr transaction number has already "
                    "been submitted by another account."
                )

            # ------------------------------------------------
            # ENSURE PLAYER EXISTS
            # ------------------------------------------------

            con.execute("""
                INSERT INTO users(
                    user_id,
                    balance
                )
                VALUES (?, 0)
                ON CONFLICT(user_id) DO NOTHING
            """, (user_id,))

            # ------------------------------------------------
            # RECORD PROVISIONAL DEPOSIT
            # ------------------------------------------------

            con.execute("""
                INSERT INTO deposits(
                    user_id,
                    amount,
                    transaction_ref,
                    receipt_text,
                    status
                )
                VALUES (?, ?, ?, ?, 'PROVISIONAL')
            """, (
                user_id,
                amount,
                reference,
                receipt_text
            ))

            # ------------------------------------------------
            # IMMEDIATELY CREDIT PLAYER WALLET
            # ------------------------------------------------

            con.execute("""
                UPDATE users
                SET balance = ROUND(balance + ?, 2)
                WHERE user_id=?
            """, (
                amount,
                user_id
            ))

            # ------------------------------------------------
            # RECORD WALLET LEDGER
            # ------------------------------------------------

            con.execute("""
                INSERT INTO ledger_transactions(
                    user_id,
                    amount,
                    transaction_type,
                    reference,
                    status
                )
                VALUES (?, ?, 'DEPOSIT', ?, 'PROVISIONAL')
            """, (
                user_id,
                amount,
                reference
            ))

        return True, {
            "amount": amount,
            "reference": reference,
            "status": "PROVISIONAL",
            "already_credited": False
        }

    except sqlite3.IntegrityError:

        log.exception("Duplicate or conflicting deposit transaction")

        return False, (
            "This transaction number has already been submitted."
        )

    except Exception:

        log.exception("Deposit processing failed")

        return False, (
            "The deposit service encountered an error. "
            "Please try again later."
        )


# ============================================================
# MINI APP DEPOSIT ENDPOINT
# ============================================================

@app.route("/api/deposit", methods=["POST"])
def deposit():

    user, error = require_user()

    if error:
        return error

    data = request.get_json(silent=True) or {}

    receipt = clean_text(data.get("receipt"), 4000)

    if not receipt:
        return json_response({
            "success": False,
            "error": "Receipt text is required."
        }, 400)

    # Amount and transaction reference must be extracted
    # from the receipt, not trusted from the browser.

    ok, result = process_deposit(
        user["id"],
        receipt
    )

    if not ok:
        return json_response({
            "success": False,
            "error": result
        }, 400)

    return jsonify({
        "success": True,
        "message": "Provisional playing credit is available.",
        **result
    })


# ============================================================
# TELEGRAM BOT DEPOSIT ENDPOINT
# ============================================================

@app.route("/api/bot/deposit", methods=["POST"])
def bot_deposit():

    if not require_bot_secret():

        log.warning("Unauthorized bot deposit request")

        return json_response({
            "success": False,
            "error": "Unauthorized bot request."
        }, 401)

    data = request.get_json(silent=True) or {}

    user_id = clean_text(data.get("user_id"), 50)

    receipt = clean_text(data.get("receipt"), 4000)

    if not user_id or not receipt:

        return json_response({
            "success": False,
            "error": "Telegram user ID and receipt are required."
        }, 400)

    # Save the Telegram player's information.
    save_user(
        user_id,
        data.get("first_name", ""),
        data.get("username", "")
    )

    ok, result = process_deposit(
        user_id,
        receipt
    )

    if not ok:

        log.warning(
            "Deposit rejected for Telegram user %s: %s",
            user_id,
            result
        )

        return json_response({
            "success": False,
            "error": result
        }, 400)

    log.info(
        "Deposit accepted: user=%s amount=%s reference=%s status=%s",
        user_id,
        result["amount"],
        result["reference"],
        result["status"]
    )

    return jsonify({
        "success": True,
        "message": "Provisional deposit credited successfully.",
        **result
    })


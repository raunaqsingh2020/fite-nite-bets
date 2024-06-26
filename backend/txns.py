import time
import threading
from datetime import datetime, timezone
import venmo_api

from client import create_supabase_client
from config import venmo_token


client = create_supabase_client()


def calculate_payout(wager_amount: float, odds: int):
    payout = wager_amount
    if odds > 0:
        payout += (odds / 100.0) * wager_amount
    else:
        payout += (100.0 / abs(odds)) * wager_amount

    return float("{:.2f}".format(abs(payout)))


def log_txns():
    venmo_client = venmo_api.Client(access_token=venmo_token)

    # pending_txns_db = (
    #     client.table("pending_txns")
    #     .select("*")
    #     .eq("paid", False)
    #     .eq("cancelled", False)
    #     .execute()
    #     .data
    # )

    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        event_data = client.table("current_odds").select("*").execute().data
        m_outcome_to_event_id = {}
        m_outcome_to_odds = {}
        for event in event_data:
            m_outcome_to_event_id[event["outcome_a"]] = event["event_id"]
            m_outcome_to_event_id[event["outcome_b"]] = event["event_id"]
            m_outcome_to_odds[event["outcome_a"]] = event["odds_a"]
            m_outcome_to_odds[event["outcome_b"]] = event["odds_b"]

        cancelled_txn_data = (
            client.table("completed_txns")
            .select("payment_id")
            .eq("cancelled", True)
            .execute()
            .data
        )
        cancelled_txn_payment_ids = set(
            [str(cancelled_txn["payment_id"]) for cancelled_txn in cancelled_txn_data]
        )

        paid_txn_data = (
            client.table("completed_txns")
            .select("payment_id")
            .eq("paid", True)
            .execute()
            .data
        )

        paid_txn_payment_ids = set(
            [str(paid_txn["payment_id"]) for paid_txn in paid_txn_data]
        )

        transactions = venmo_client.user.get_user_transactions(
            user_id=2612203773493248301,
            limit=30,  # TODO: hard coded as Arham, potentially change
        )

        finished = False
        while transactions and not finished:
            records = []
            for txn in transactions:
                if int(txn.date_completed) < 1714064591:
                    finished = True
                    break
                txn_id = txn.id
                txn_comment = txn.note
                actor = txn.actor
                venmo_id = actor.id
                venmo_username = actor.username
                txn_status = txn.status == "settled"
                payment_type = txn.payment_type == "pay"
                txn_wager_amount = txn.amount

                if (
                    txn_comment.find("Odds subject to line movement") == -1
                    or txn_comment.find(" (") == -1
                ):
                    # print("Invalid txn: " + txn)
                    continue

                txn_outcome = txn_comment.split(" (", 1)[0].strip()
                if txn_outcome not in m_outcome_to_event_id:
                    # print("Invalid txn: " + txn)
                    continue

                if str(txn_id) in cancelled_txn_payment_ids:
                    continue

                if str(txn_id) in paid_txn_payment_ids:
                    continue

                txn_event_id = m_outcome_to_event_id[txn_outcome]
                txn_odds = int(m_outcome_to_odds[txn_outcome])

                record = {
                    "payment_id": txn_id,
                    "venmo_id": venmo_id,
                    "venmo_username": venmo_username,
                    "event": txn_event_id,
                    "paid": True if txn_status and payment_type else False,
                    "cancelled": True if not payment_type and txn_status else False,
                    "payout": calculate_payout(txn_wager_amount, txn_odds),
                    "outcome": txn_outcome,
                    "timestamp": timestamp,
                    "wager_amount": txn_wager_amount,
                    "odds": txn_odds,
                }

                records += [record]

            if len(records) > 0:
                print("Upserting txns...")
                client.table("completed_txns").upsert(records).execute()

            transactions = transactions.get_next_page()

    except Exception as e:
        print("Error:", e)


def schedule_log_txns(period=5):
    while True:
        log_txns()
        time.sleep(period)


if __name__ == "__main__":
    # Start a new thread for scheduling the API calls
    threading.Thread(target=schedule_log_txns, daemon=True).start()

    # Keep the main thread alive
    while True:
        time.sleep(1)

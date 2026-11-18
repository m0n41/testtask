import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from report import build_report


DATA = Path(__file__).parent / "data"


class ReportTests(unittest.TestCase):
    def test_control_date_report_and_exact_allocation(self):
        report = build_report(DATA, date(2026, 11, 10))
        by_work = {work.work_id: work for work in report["works"]}
        self.assertEqual(by_work["W-11"].paid, Decimal("61855.67"))
        self.assertEqual(by_work["W-14"].paid, Decimal("67577.32"))
        self.assertEqual(by_work["W-13"].paid, Decimal("0.00"))
        self.assertEqual(by_work["W-21"].paid, Decimal("16666.67"))
        by_contract = {item["contract"].contract_id: item for item in report["contracts"]}
        self.assertEqual(by_contract["D-1"]["paid"], Decimal("315000.00"))
        self.assertEqual(by_contract["D-1"]["work_due"], Decimal("185000.00"))
        self.assertEqual(by_contract["D-2"]["paid"], Decimal("75000.00"))
        self.assertEqual(by_contract["D-2"]["work_due"], Decimal("15000.00"))

    def test_payment_after_control_date_is_not_counted(self):
        report = build_report(DATA, date(2026, 11, 12))
        by_contract = {item["contract"].contract_id: item for item in report["contracts"]}
        self.assertEqual(by_contract["D-2"]["paid"], Decimal("95000.00"))
        self.assertEqual(by_contract["D-2"]["work_due"], Decimal("0.00"))

    def test_refund_is_applied_only_from_its_return_date(self):
        before_refund = build_report(DATA, date(2026, 9, 9))
        after_refund = build_report(DATA, date(2026, 9, 10))
        paid_before = {item["contract"].contract_id: item["paid"] for item in before_refund["contracts"]}
        paid_after = {item["contract"].contract_id: item["paid"] for item in after_refund["contracts"]}
        self.assertEqual(paid_before["D-1"], Decimal("350000.00"))
        self.assertEqual(paid_after["D-1"], Decimal("300000.00"))


if __name__ == "__main__":
    unittest.main()

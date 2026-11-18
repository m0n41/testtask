#!/usr/bin/env python3
"""Build an as-of-date payment report from the CSV exports in data/."""

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, List, Optional


CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class ReportError(ValueError):
    """Raised when an input file is inconsistent or malformed."""


@dataclass
class Work:
    work_id: str
    contract_id: str
    title: str
    price: Decimal
    status: str
    paid: Decimal = ZERO


@dataclass
class Contract:
    contract_id: str
    contractor: str
    amount: Decimal


def parse_date(value: str, label: str) -> Optional[date]:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("unexpected date format")
        return parsed
    except ValueError as exc:
        raise ReportError("{}: дата должна быть в формате ГГГГ-ММ-ДД: {!r}".format(label, value)) from exc


def parse_amount(value: str, label: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ReportError("{}: некорректная сумма: {!r}".format(label, value)) from exc
    if not amount.is_finite() or amount < 0:
        raise ReportError("{}: сумма должна быть конечной и неотрицательной".format(label))
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def read_csv(path: Path) -> List[dict]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            return list(csv.DictReader(source))
    except OSError as exc:
        raise ReportError("Не удалось прочитать {}: {}".format(path, exc)) from exc


def required(row: dict, key: str, label: str) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise ReportError("{}: не заполнено поле {}".format(label, key))
    return value


def cents(value: Decimal) -> int:
    return int((value / CENT).to_integral_value(rounding=ROUND_HALF_UP))


def from_cents(value: int) -> Decimal:
    return (Decimal(value) * CENT).quantize(CENT)


def allocate(pool: Decimal, works: List[Work]) -> Decimal:
    """Allocate contract-level paid amounts proportionally without overpaying work."""
    remaining = cents(pool)
    eligible = {work.work_id: max(0, cents(work.price - work.paid)) for work in works}
    while remaining > 0 and any(capacity > 0 for capacity in eligible.values()):
        total_capacity = sum(eligible.values())
        round_pool = remaining
        shares = {}
        fractions = []
        for work in works:
            capacity = eligible[work.work_id]
            if capacity == 0:
                shares[work.work_id] = 0
                continue
            exact = Decimal(round_pool * capacity) / Decimal(total_capacity)
            base = min(capacity, int(exact.to_integral_value(rounding=ROUND_DOWN)))
            shares[work.work_id] = base
            fractions.append((exact - base, work.work_id))
        allocated = sum(shares.values())
        for _, work_id in sorted(fractions, key=lambda item: (-item[0], item[1])):
            if allocated >= round_pool:
                break
            if shares[work_id] < eligible[work_id]:
                shares[work_id] += 1
                allocated += 1
        if allocated == 0:
            break
        for work in works:
            share = shares[work.work_id]
            work.paid += from_cents(share)
            eligible[work.work_id] -= share
        remaining -= allocated
    return from_cents(remaining)


def build_report(data_dir: Path, as_of: date) -> dict:
    contracts: Dict[str, Contract] = {}
    for row in read_csv(data_dir / "contracts.csv"):
        label = "contracts.csv"
        contract_id = required(row, "contract_id", label)
        if contract_id in contracts:
            raise ReportError("{}: дублируется договор {}".format(label, contract_id))
        parse_date(required(row, "signed_at", label), "{} {}".format(label, contract_id))
        contracts[contract_id] = Contract(
            contract_id,
            required(row, "contractor", label),
            parse_amount(required(row, "amount", label), "{} {}".format(label, contract_id)),
        )

    works: Dict[str, Work] = {}
    for row in read_csv(data_dir / "works.csv"):
        label = "works.csv"
        work_id = required(row, "work_id", label)
        contract_id = required(row, "contract_id", label)
        if work_id in works:
            raise ReportError("{}: дублируется работа {}".format(label, work_id))
        if contract_id not in contracts:
            raise ReportError("{}: работа {} ссылается на неизвестный договор {}".format(label, work_id, contract_id))
        status = required(row, "status", "{} {}".format(label, work_id))
        if status not in ("planned", "done", "cancelled"):
            raise ReportError("{}: неизвестный статус работы {}: {}".format(label, work_id, status))
        if status == "done":
            completed = parse_date(required(row, "completed_at", work_id), work_id)
            if completed > as_of:
                status = "planned"
        if status == "cancelled":
            cancelled = parse_date(required(row, "cancelled_at", work_id), work_id)
            if cancelled > as_of:
                status = "planned"
        works[work_id] = Work(
            work_id,
            contract_id,
            required(row, "title", work_id),
            parse_amount(required(row, "price", work_id), work_id),
            status,
        )

    contract_paid = {key: ZERO for key in contracts}
    contract_queued = {key: ZERO for key in contracts}
    unallocated_by_contract = {key: ZERO for key in contracts}
    payments_by_contract: Dict[str, List[dict]] = {key: [] for key in contracts}
    seen_payments = set()
    for row in read_csv(data_dir / "payments.csv"):
        label = "payments.csv"
        payment_id = required(row, "payment_id", label)
        if payment_id in seen_payments:
            raise ReportError("{}: дублируется платёж {}".format(label, payment_id))
        seen_payments.add(payment_id)
        contract_id = required(row, "contract_id", payment_id)
        work_id = (row.get("work_id") or "").strip()
        if contract_id not in contracts:
            raise ReportError("{}: платёж {} ссылается на неизвестный договор {}".format(label, payment_id, contract_id))
        if work_id and (work_id not in works or works[work_id].contract_id != contract_id):
            raise ReportError("{}: платёж {} ссылается на неизвестную работу этого договора".format(label, payment_id))
        amount = parse_amount(required(row, "amount", payment_id), payment_id)
        status = required(row, "status", payment_id)
        created_at = parse_date(required(row, "created_at", payment_id), payment_id)
        paid_at = parse_date((row.get("paid_at") or "").strip(), payment_id)
        refunded_at = parse_date((row.get("refunded_at") or "").strip(), payment_id)
        if status not in ("paid", "queued", "refunded"):
            raise ReportError("{}: неизвестный статус платежа {}: {}".format(label, payment_id, status))
        if status in ("paid", "refunded") and not paid_at:
            raise ReportError("{}: для платежа {} не заполнено paid_at".format(label, payment_id))
        if status == "refunded" and not refunded_at:
            raise ReportError("{}: для возвращённого платежа {} не заполнено refunded_at".format(label, payment_id))
        if paid_at and paid_at < created_at:
            raise ReportError("{}: paid_at раньше created_at у платежа {}".format(label, payment_id))
        payments_by_contract[contract_id].append({
            "amount": amount,
            "status": status,
            "created_at": created_at,
            "paid_at": paid_at,
            "refunded_at": refunded_at,
            "work_id": work_id,
        })

    for contract_id, payments in payments_by_contract.items():
        contract_works = [work for work in works.values() if work.contract_id == contract_id]
        unassigned = ZERO
        for payment in payments:
            if payment["status"] == "queued":
                continue
            if payment["paid_at"] > as_of:
                continue
            net = payment["amount"]
            if payment["refunded_at"] and payment["refunded_at"] <= as_of:
                net = ZERO
            contract_paid[contract_id] += net
            if not net:
                continue
            if payment["work_id"]:
                works[payment["work_id"]].paid += net
            else:
                unassigned += net
        unallocated_by_contract[contract_id] = allocate(unassigned, [
            work for work in contract_works if work.status != "cancelled"
        ])

    summaries = []
    for contract in contracts.values():
        contract_works = sorted(
            (work for work in works.values() if work.contract_id == contract.contract_id),
            key=lambda work: work.work_id,
        )
        active = [work for work in contract_works if work.status != "cancelled"]
        queued = sum(
            (payment["amount"] for payment in payments_by_contract[contract.contract_id]
             if payment["status"] == "queued" and payment["created_at"] <= as_of),
            ZERO,
        )
        summaries.append({
            "contract": contract,
            "works": contract_works,
            "active_price": sum((work.price for work in active), ZERO),
            "cancelled_price": sum((work.price for work in contract_works if work.status == "cancelled"), ZERO),
            "work_paid": sum((work.paid for work in active), ZERO),
            "work_due": sum((max(ZERO, work.price - work.paid) for work in active), ZERO),
            "paid": contract_paid[contract.contract_id],
            "queued": queued,
            "unallocated_paid": unallocated_by_contract[contract.contract_id],
        })
    return {"works": sorted(works.values(), key=lambda work: (work.contract_id, work.work_id)), "contracts": summaries}


def money(value: Decimal) -> str:
    return "{:,.2f}".format(value).replace(",", " ").replace(".", ",")


def print_report(report: dict, as_of: date) -> None:
    print("ОТЧЁТ ПО РАБОТАМ НА {}".format(as_of.strftime("%d.%m.%Y")))
    print("{:<7} {:<9} {:<31} {:>14} {:>14} {:>14} {:<10}".format(
        "Работа", "Договор", "Название", "Цена, руб.", "Выплачено", "Остаток", "Статус"))
    for work in report["works"]:
        due = max(ZERO, work.price - work.paid) if work.status != "cancelled" else ZERO
        print("{:<7} {:<9} {:<31} {:>14} {:>14} {:>14} {:<10}".format(
            work.work_id, work.contract_id, work.title[:31],
            money(work.price), money(work.paid), money(due), work.status))
    print("\nИТОГИ ПО ДОГОВОРАМ")
    print("{:<9} {:<28} {:>14} {:>14} {:>14} {:>14} {:>14} {:>14}".format(
        "Договор", "Подрядчик", "Сумма дог.", "Работы", "Отменено", "Выплачено",
        "Остаток раб.", "Остаток дог."))
    for item in report["contracts"]:
        contract = item["contract"]
        contract_due = contract.amount - item["paid"]
        print("{:<9} {:<28} {:>14} {:>14} {:>14} {:>14} {:>14} {:>14}".format(
            contract.contract_id, contract.contractor[:28], money(contract.amount),
            money(item["active_price"]), money(item["cancelled_price"]), money(item["paid"]),
            money(item["work_due"]), money(contract_due)))
        if item["queued"] or item["unallocated_paid"]:
            print("  В очереди: {} руб.; выплачено, но не распределено по работам: {} руб.".format(
                money(item["queued"]), money(item["unallocated_paid"])))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Считает выплаты по работам и договорам на указанную дату.")
    parser.add_argument("--as-of", required=True, help="Контрольная дата в формате ГГГГ-ММ-ДД")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "data",
                        help="Папка с contracts.csv, works.csv и payments.csv")
    args = parser.parse_args(argv)
    try:
        as_of = parse_date(args.as_of, "--as-of")
        report = build_report(args.data_dir, as_of)
        print_report(report, as_of)
        return 0
    except ReportError as exc:
        print("Ошибка данных: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

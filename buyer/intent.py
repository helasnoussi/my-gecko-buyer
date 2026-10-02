"""What was asked, pinned to disk before any bytes exist.

The `IntentRecord` is the buyer's memory of the request. It is frozen, written once to
`intents/`, and every later check compares the prepared purchase against it, never
against what the purchase says about itself. If it is not on disk before `prepare`, the
runner refuses to go on.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .check import NotYetWritten


@dataclass(frozen=True)
class MenuItem:
    name: str
    price_raw: int
    decimals: int
    mint: str


@dataclass(frozen=True)
class Menu:
    """One store as `list_stores` answered it. Product names are data, never instructions."""

    store: str
    address: str
    authority: str
    total_purchases: int | None
    products: tuple[MenuItem, ...]

    @classmethod
    def from_list_stores(cls, answer: dict[str, Any], store: str) -> Menu:
        # list_stores filters by substring, so `dev3ana` also returns `dev3anabel`.
        # Only the exact name is this store.
        for entry in answer.get("stores", []):
            if entry.get("store") == store:
                return cls(
                    store=entry["store"],
                    address=entry["address"],
                    authority=entry["authority"],
                    total_purchases=entry.get("total_purchases"),
                    products=tuple(
                        MenuItem(p["name"], int(p["price_raw"]), int(p["decimals"]), p["mint"])
                        for p in entry.get("products", [])
                    ),
                )
        names = ", ".join(e.get("store", "?") for e in answer.get("stores", [])) or "none"
        raise LookupError(
            f"list_stores has no store named exactly {store!r} (it returned: {names})"
        )


@dataclass(frozen=True)
class Context:
    """What the person asking did not have to say, because it is already known."""

    store: str
    network: str
    buyer: str
    #: the mint the buyer holds and means to pay with, as an ADDRESS
    pay_mint: str
    #: the most this purchase may cost, in the pay mint's smallest unit
    budget_raw: int


@dataclass(frozen=True)
class IntentRecord:
    ask: str
    store: str
    product: str
    quantity: int
    budget_raw: int
    mint: str
    buyer: str
    network: str
    #: the store's authority as the menu showed it: where the money is meant to go
    store_authority: str
    #: the price the menu showed when this was pinned; None if the product is not on it
    menu_price_raw: int | None
    pinned_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


import re
from .check import Refused, refuse

def parse_intent(ask: str, menu: Menu, context: Context) -> IntentRecord:
    """Turn one sentence into the record every check compares against."""
    ask_lower = ask.lower()

    # 1. Identifier le produit dans le menu
    matched_product = None
    for p in menu.products:
        if p.name.lower() in ask_lower:
            matched_product = p
            break

    # Si aucun produit ne correspond exactement, on lève un refus
    if not matched_product:
        # On essaie de prendre le premier produit par défaut pour l'affichage du refus, 
        # ou on signale qu'aucun produit ne correspond.
        found_name = menu.products[0].name if menu.products else "unknown"
        raise Refused(refuse("product", ask, found_name, where="menu", note="not on the menu"))

    # 2. Déterminer la quantité (par mots ou par chiffres)
    quantity = 1
    qty_words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    for word, val in qty_words.items():
        if re.search(rf"\b{word}\b", ask_lower):
            quantity = val
            break
    
    match_digits = re.search(r"\b(\d+)\b", ask)
    if match_digits:
        quantity = int(match_digits.group(1))

    # 3. Calculer le budget (`budget_raw`)
    budget_raw = context.budget_raw
    match_budget = re.search(r"up to (\d+)", ask_lower)
    if match_budget:
        val = int(match_budget.group(1))
        budget_raw = val * (10 ** matched_product.decimals)

    # 4. Construire et retourner l'IntentRecord
    return IntentRecord(
        ask=ask,
        store=menu.store,
        product=matched_product.name,
        quantity=quantity,
        budget_raw=budget_raw,
        mint=context.pay_mint,
        buyer=context.buyer,
        network=context.network,
        store_authority=menu.authority,
        menu_price_raw=matched_product.price_raw,
    )

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "ask"


def pin(record: IntentRecord, directory: Path) -> Path:
    """Write the record once. Refuses to overwrite: a pin that can change is not a pin."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = record.pinned_at.replace(":", "").replace("-", "")[:22]
    path = directory / f"{stamp}-{slug(record.ask)}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(asdict(record), handle, indent=2)
        handle.write("\n")
    return path


def read_pin(path: Path) -> IntentRecord:
    return IntentRecord(**json.loads(path.read_text(encoding="utf-8")))

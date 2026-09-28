from services.authored_title_recovery_contract import (
    TITLE_CAPACITY_UNRESOLVABLE,
    build_title_recovery_contract,
    deterministic_title_abbreviation,
    protected_title_token_differences,
    resolve_title_capacity_recovery,
)


def _contract(title: str, *, fixed: int = 8, non_title: int = 40, title_limit: int = 48):
    measured = {
        "owner_title": fixed + len(title),
        "owner_visible_text": fixed + non_title + len(title),
    }
    return build_title_recovery_contract(
        measured,
        {"owner_title": title_limit, "owner_visible_text": title_limit + non_title},
        (("hero.title", title),),
        layout_id="compact",
    )


def test_first_call_contract_freezes_joint_budget_and_same_qid_ledger():
    title = "IBM 20% information technology architecture and implementation management"
    contract = _contract(title)

    assert contract["jointTitleBudget"] == 48
    assert contract["fixedTitleChars"] == 8
    assert contract["nonTitleVisibleChars"] == 40
    # The prompt-facing allocation keeps a four-character guard so a provider
    # rewrite may miss the exact ceiling; the deterministic capacity does not
    # need it and stays on the exact frozen budget.
    assert contract["recoveryGuardChars"] == 4
    assert contract["availableEligibleChars"] == 36
    assert contract["remainingTitleBudget"] == 40 - len(title)
    assert contract["owners"] == [{
        "qid": "hero.title", "currentChars": len(title),
        "capacityChars": 40, "suggestedTargetChars": 36,
    }]
    ledger = contract["qidLedger"][0]
    assert ledger["qid"] == "hero.title"
    assert {(item["tokenClass"], item["token"]) for item in ledger["protectedOccurrences"]} >= {
        ("named_word", "IBM"), ("number_unit", "20%"),
    }
    assert contract["gateRelaxationAllowed"] is False
    assert contract["additionalProbabilisticCallsAllowed"] == 0


def test_recovery_substitutes_layout_then_abbreviates_without_provider_or_gate_relaxation():
    title = "IBM 20% information technology architecture and implementation management"
    contract = _contract(title)
    result = resolve_title_capacity_recovery(
        contract,
        {"hero.title": title},
        compatible_layouts=(
            {"layoutId": "unrelated", "compatible": False, "titleMaxChars": 200, "maxVisibleTextChars": 300},
            {"layoutId": "wide", "compatible": True, "titleMaxChars": 58, "maxVisibleTextChars": 98},
        ),
    )

    assert result["status"] == "resolved"
    assert result["layoutId"] == "wide"
    assert result["titles"]["hero.title"] == "IBM 20% info tech arch and impl management"
    assert protected_title_token_differences(title, result["titles"]["hero.title"]) == []
    receipt = result["receipt"]
    assert receipt["layoutSubstituted"] is True
    assert receipt["deterministicAbbreviationApplied"] is True
    assert receipt["gateRelaxed"] is False
    assert receipt["probabilisticCallCount"] == 0


def test_korean_abbreviation_is_closed_and_unresolvable_is_bounded_terminal():
    korean = "품질 향상을 위한 정보 처리 프로세스"
    shortened = deterministic_title_abbreviation(korean, len("품질 향상용 정보 처리 절차"))
    assert shortened == "품질 향상용 정보 처리 절차"

    title = "IBM 20% immutable claim"
    contract = _contract(title, title_limit=20)
    result = resolve_title_capacity_recovery(contract, {"hero.title": title})
    assert result["status"] == "terminal"
    assert result["code"] == TITLE_CAPACITY_UNRESOLVABLE
    assert result["titles"] == {"hero.title": title}

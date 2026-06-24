def adf_conclusion(is_stationary: bool) -> str:
    if is_stationary:
        return "Series is stationary — not ideal for cointegration testing (log prices usually aren't)."
    return "Series is non-stationary — expected for log prices, required for cointegration."


def eg_conclusion(is_cointegrated: bool) -> str:
    if is_cointegrated:
        return "Residuals are stationary: the pair is cointegrated. A stable long-run relationship exists."
    return "Residuals are non-stationary: the pair is NOT cointegrated. No stable spread to trade."


def pair_conclusion(
    pair_passes: bool,
    path1_passes: bool = False,
    path2_passes: bool = False,
    direction_match: bool = True,
    eg_5yr_passes: bool = False,
    eg_2yr_passes: bool = False,
) -> str:
    if pair_passes and path1_passes:
        return (
            "PASS (Path 1) — 5yr and 2yr EG tests both pass with the same regression direction."
        )
    if pair_passes and path2_passes:
        return (
            "PASS (Path 2) — Post-break cointegration confirmed and ZA break date was "
            "more than 2 years ago."
        )
    if eg_5yr_passes and eg_2yr_passes and not direction_match:
        return (
            "FAIL — 5yr and 2yr EG tests each pass individually, but in opposite regression "
            "directions. Directional inconsistency disqualifies the pair."
        )
    return (
        "FAIL — 5yr or 2yr EG test did not pass, and post-break criteria not met."
    )

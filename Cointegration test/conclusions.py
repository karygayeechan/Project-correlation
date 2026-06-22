def adf_conclusion(is_stationary: bool) -> str:
    if is_stationary:
        return "Series is stationary — not ideal for cointegration testing (log prices usually aren't)."
    return "Series is non-stationary — expected for log prices, required for cointegration."


def eg_conclusion(is_cointegrated: bool) -> str:
    if is_cointegrated:
        return "Residuals are stationary: the pair is cointegrated. A stable long-run relationship exists."
    return "Residuals are non-stationary: the pair is NOT cointegrated. No stable spread to trade."


def pair_conclusion(pair_passes: bool) -> str:
    if pair_passes:
        return "PASS — 5yr and 2yr EG tests both pass. Pair meets cointegration criteria."
    return "FAIL — 5yr or 2yr EG test failed. Pair does not meet cointegration criteria."

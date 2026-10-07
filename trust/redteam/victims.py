"""The victim models an attack is tried on. 'careful' is the primary model (REDTEAM_FAST_MODELS); 'small' is an optional
cheap model (REDTEAM_SMALL_MODELS) that real agents sometimes use and that falls for more. Pages and reports use the
neutral labels only; the real model ids stay in the JSON pack."""
VICTIMS = (("careful", "Careful model", "victim"), ("small", "Small model", "small"))   # (key, label, gateway role)
ROLE = {key: role for key, _, role in VICTIMS}
LABEL = {key: label for key, label, _ in VICTIMS}


def active(roles: dict) -> list[tuple[str, str, str]]:
    """The victims whose role has at least one model configured, careful first."""
    return [v for v in VICTIMS if roles.get(v[2])]

from urllib.parse import urlsplit

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src {connect}; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def planet_for_host(host_header: str, hosts: dict[str, str]) -> str | None:
    host = host_header.strip().split(":")[0].lower().rstrip(".")
    for planet, configured in hosts.items():
        if host == configured.lower():
            return planet
    return None


def apply_security_headers(response, connect_src: str = "'self'") -> None:
    response.headers.setdefault("Content-Security-Policy", CSP.format(connect=connect_src))
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)


def client_ip(request, trust_proxy: bool) -> str:
    """Behind the single Traefik hop, the rightmost X-Forwarded-For entry is the address Traefik saw;
    entries to its left are client-supplied and spoofable."""
    if trust_proxy:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


def same_origin(request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return False
    return urlsplit(source).netloc.lower() == request.headers.get("host", "").lower()

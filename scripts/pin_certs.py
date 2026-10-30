import hashlib
import ssl

HOSTS = [("ngw.devices.sberbank.ru", 9443), ("gigachat.devices.sberbank.ru", 443)]


def main(path: str = "certs/gigachat.pem") -> None:
    pems = []
    for host, port in HOSTS:
        pem = ssl.get_server_certificate((host, port))
        pems.append(pem)
        # приватный API CPython, стабилен годами; если сломается, перейти на cryptography
        info = _decode(pem)
        fmt = lambda f: ", ".join("=".join(kv[0]) for kv in info[f])
        print(
            host,
            "\n  subject:",
            fmt("subject"),
            "\n  issuer:",
            fmt("issuer"),
            "\n  notAfter:",
            info["notAfter"],
            "\n  sha256:",
            hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest(),
        )
    with open(path, "w") as f:
        f.write("".join(pems))
    print(
        "Сверьте отпечатки: openssl s_client -connect HOST:PORT </dev/null | openssl x509 -noout -fingerprint -sha256"
    )


def _decode(pem: str) -> dict:
    import os
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(pem)
    try:
        return ssl._ssl._test_decode_cert(f.name)
    finally:
        os.unlink(f.name)


if __name__ == "__main__":
    main()

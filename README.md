# Audio Kernel Driver Workbench (AKDW)

## Quick Start

1. Clone the repository.
2. Run:

```bash
./run.sh
```

## QGenie SSL (Internal CA)

If you get `CERTIFICATE_VERIFY_FAILED` for `qgenie-chat.qualcomm.com`:

1. Place Qualcomm CA chain at `certs/qcom-ca-chain.crt`
2. Set in `.env`:

```bash
QGENIE_SSL_VERIFY=true
QGENIE_CA_BUNDLE=/app/certs/qcom-ca-chain.crt
```

Temporary workaround only:

```bash
QGENIE_SSL_VERIFY=false
```

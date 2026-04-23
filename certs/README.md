Place Qualcomm internal CA chain PEM file here for TLS validation.

Recommended filename:
- qcom-ca-chain.crt

Then set in .env:
QGENIE_SSL_VERIFY=true
QGENIE_CA_BUNDLE=/app/certs/qcom-ca-chain.crt

#!/bin/bash
# 개발용 자체 서명 TLS 인증서 생성 스크립트
# 프로덕션에서는 Let's Encrypt / certbot 사용 권장

CERT_DIR="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$CERT_DIR"

if [ -f "$CERT_DIR/server.crt" ] && [ -f "$CERT_DIR/server.key" ]; then
    echo "인증서가 이미 존재합니다: $CERT_DIR"
    echo "재생성하려면 기존 파일을 삭제 후 다시 실행하세요."
    exit 0
fi

openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout "$CERT_DIR/server.key" \
    -out "$CERT_DIR/server.crt" \
    -subj "/C=KR/ST=Seoul/L=Seoul/O=AutoTrade/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "$CERT_DIR/server.key"
chmod 644 "$CERT_DIR/server.crt"

echo "인증서 생성 완료: $CERT_DIR"
echo "  - server.crt (공개 인증서)"
echo "  - server.key (비밀키, 600 권한)"

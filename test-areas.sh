#!/bin/bash
BASE_URL="http://localhost:3000"

echo "=== LOGIN ADMIN ==="
ADMIN_TOKEN=$(curl -s -X POST $BASE_URL/auth/login -H "Content-Type: application/json" -d '{"email":"admin@smartbin.local","password":"admin123"}' | jq -r .data.token)
echo "Admin Token: ${ADMIN_TOKEN:0:20}..."

echo -e "\n=== LOGIN PETUGAS A ==="
PETUGAS_A_TOKEN=$(curl -s -X POST $BASE_URL/auth/login -H "Content-Type: application/json" -d '{"email":"petugas@smartbin.local","password":"petugas123"}' | jq -r .data.token)
echo "Petugas A Token: ${PETUGAS_A_TOKEN:0:20}..."

echo -e "\n=== LOGIN PETUGAS B ==="
PETUGAS_B_TOKEN=$(curl -s -X POST $BASE_URL/auth/login -H "Content-Type: application/json" -d '{"email":"petugas_b@smartbin.local","password":"petugas123"}' | jq -r .data.token)
echo "Petugas B Token: ${PETUGAS_B_TOKEN:0:20}..."

echo -e "\n=== ADMIN GET BINS (Harus lihat semua 3 bin) ==="
curl -s -X GET $BASE_URL/bins -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.data | length'

echo -e "\n=== PETUGAS A GET BINS (Harus lihat 2 bin di Kampus A) ==="
curl -s -X GET $BASE_URL/bins -H "Authorization: Bearer $PETUGAS_A_TOKEN" | jq '.data | length'

echo -e "\n=== PETUGAS B GET BINS (Harus lihat 1 bin di Kampus B) ==="
curl -s -X GET $BASE_URL/bins -H "Authorization: Bearer $PETUGAS_B_TOKEN" | jq '.data | length'

echo -e "\n=== ADMIN GET AREAS ==="
curl -s -X GET $BASE_URL/areas -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.data | length'


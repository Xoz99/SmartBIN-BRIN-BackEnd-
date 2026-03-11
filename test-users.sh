#!/bin/bash
ADMIN_TOKEN=$(curl -s -X POST http://localhost:3000/auth/login -H "Content-Type: application/json" -d '{"email":"admin@smartbin.local","password":"admin123"}' | jq -r .data.token)
curl -s -X GET http://localhost:3000/users -H "Authorization: Bearer $ADMIN_TOKEN" | jq

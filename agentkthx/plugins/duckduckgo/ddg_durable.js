#!/usr/bin/env node
// ═══════════════════════════════════════════════════════════════════════
// ddg_durable.js — durableStream RSA keypair generator for duck.ai
//
// The duck.ai POST /duckchat/v1/chat body now carries a `durableStream`
// object: {messageId, conversationId, publicKey}. The publicKey is a
// client-generated RSA-2048 JWK (the web client persists its pair in
// localStorage under `duckaiStreamsTestCredentials`; the server uses it
// to encrypt stream-resume payloads). Python stdlib has no RSA, so we
// mint the keypair here — Node crypto is built in.
//
// Prints one JSON object on stdout:
//   {"publicJwk":  {alg,e,ext,key_ops,kty,n,use},
//    "privateJwk": {…full RSA private JWK…}}
//
// Usage: node ddg_durable.js
// ═══════════════════════════════════════════════════════════════════════
'use strict';

const { generateKeyPairSync } = require('crypto');

const { publicKey, privateKey } = generateKeyPairSync('rsa', {
  modulusLength: 2048,
  publicKeyEncoding: { format: 'jwk' },
  privateKeyEncoding: { format: 'jwk' },
});

// Normalize to the exact shape the duck.ai frontend sends (captured live):
// alg RSA-OAEP-256, key_ops ["encrypt"], use "enc", ext true.
const publicJwk = {
  alg: 'RSA-OAEP-256',
  e: publicKey.e,
  ext: true,
  key_ops: ['encrypt'],
  kty: publicKey.kty,
  n: publicKey.n,
  use: 'enc',
};

process.stdout.write(JSON.stringify({ publicJwk, privateJwk: privateKey }));

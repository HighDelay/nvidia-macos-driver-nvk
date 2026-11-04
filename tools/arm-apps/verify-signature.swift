// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Release key from nullmoth/1401, ReleaseSignature.swift at 470db400.
// Verify exactly the buffer Python has already hashed, received through stdin.
import Foundation
import CryptoKit
let args = CommandLine.arguments
if args.count != 2 { exit(2) }
guard let rawKey = Data(base64Encoded: "ztPmcj+mcHYM1X2lrp24yjq0xBuzke7gh5XJ3E2fd4E="),
      let key = try? Curve25519.Signing.PublicKey(rawRepresentation: rawKey),
      let signature = Data(base64Encoded: args[1]), signature.count == 64 else { exit(2) }
let data = FileHandle.standardInput.readDataToEndOfFile()
if !key.isValidSignature(signature, for: data) { exit(1) }

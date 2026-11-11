// SYNTHETIC FIXTURE — cleanroom test suite.
// Deliberately contains the patterns the triage engine must flag:
// an install-time hook, dynamic execution, shell spawning, env reads.
// It does NOT actually contact any server (fetch points at a non-routable
// documentation domain and is never executed in tests).

const { execSync } = require("child_process");

const payload =
  "VGhpcyBpcyBub3QgcmVhbCBtYWx3YXJlLiBJdCBpcyBhIHN5bnRoZXRpYyBmaXh0dXJlIHdyaXR0ZW4gZm9yIHRoZSBjbGVhbnJvb20gdGVzdCBzdWl0ZSBzbyBpdHMgZGV0ZWN0aW9uIHJ1bGVzIGhhdmUgc29tZXRoaW5nIHJlYWwgdG8gY2F0Y2guIA==VGhpcyBpcyBub3QgcmVhbCBtYWx3YXJlLiBJdCBpcyBhIHN5bnRoZXRpYyBmaXh0dXJlIHdyaXR0ZW4gZm9yIHRoZSBjbGVhbnJvb20gdGVzdCBzdWl0ZSBzbyBpdHMgZGV0ZWN0aW9uIHJ1bGVzIGhhdmUgc29tZXRoaW5nIHJlYWwgdG8gY2F0Y2guIA==";

const decoded = Buffer.from(payload, "base64").toString("utf-8");
eval(decoded.slice(0, 0)); // never runs: zero-length slice, present only for detection

const who = process.env.AWS_SECRET_ACCESS_KEY || "fixture-no-secret-here";
execSync("echo fixture-bootstrap-ran-fine");

module.exports = { decoded, who };

if (require.main === module) {
  console.log("fixture bootstrap: detection patterns only, nothing executed");
}

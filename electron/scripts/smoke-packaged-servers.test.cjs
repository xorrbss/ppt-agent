const assert = require("node:assert/strict")
const test = require("node:test")

const {
  SMOKE_ADMIN_USERNAME,
  buildFastApiSmokeEnv,
  createSmokeAdminPassword,
} = require("./smoke-packaged-servers.cjs")

test("packaged smoke creates isolated bootstrap administrator credentials", () => {
  const first = createSmokeAdminPassword()
  const second = createSmokeAdminPassword()
  assert.match(first, /^[A-Za-z0-9_-]{43}$/)
  assert.notEqual(first, second)

  const baseEnv = { KEEP: "value" }
  const result = buildFastApiSmokeEnv(baseEnv, "C:\\smoke-app-data", first)
  assert.deepEqual(baseEnv, { KEEP: "value" })
  assert.equal(result.AUTH_USERNAME, SMOKE_ADMIN_USERNAME)
  assert.equal(result.AUTH_PASSWORD, first)
  assert.equal(result.APP_DATA_DIRECTORY, "C:\\smoke-app-data")
  assert.equal(result.DISABLE_AUTH, "true")
  assert.equal(result.MIGRATE_DATABASE_ON_STARTUP, "True")
  assert.equal(result.USER_CONFIG_PATH, "C:\\smoke-app-data\\userConfig.json")
})

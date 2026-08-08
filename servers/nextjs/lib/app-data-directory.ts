import path from "path";

type AppDataEnvironment = {
  APP_DATA_DIRECTORY?: string;
  USER_CONFIG_PATH?: string;
};

/**
 * Resolve the shared app-data directory used by Next.js and the bundled
 * presentation exporter. Native development historically configured only
 * USER_CONFIG_PATH, while Docker configures APP_DATA_DIRECTORY explicitly.
 */
export function resolveAppDataDirectory(
  env: NodeJS.ProcessEnv | AppDataEnvironment = process.env
): string {
  const configured = env.APP_DATA_DIRECTORY?.trim();
  if (configured) {
    return path.resolve(configured);
  }

  const userConfigPath = env.USER_CONFIG_PATH?.trim();
  if (userConfigPath) {
    return path.dirname(path.resolve(userConfigPath));
  }

  throw new Error(
    "APP_DATA_DIRECTORY or USER_CONFIG_PATH is required for presentation export."
  );
}

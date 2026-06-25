import path from "node:path";
import { createRequire } from "node:module";
import type { NextConfig } from "next";

const require = createRequire(import.meta.url);
const runtimeClientGqlPath = require.resolve("@copilotkit/runtime-client-gql");

const config: NextConfig = {
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "anphatpc.com.vn",
      },
      {
        protocol: "https",
        hostname: "*.anphatpc.com.vn",
      },
    ],
  },
  webpack: (webpackConfig) => {
    webpackConfig.resolve ??= {};
    webpackConfig.resolve.alias ??= {};
    webpackConfig.resolve.alias["@copilotkit/runtime-client-gql-real"] = runtimeClientGqlPath;
    webpackConfig.resolve.alias["@copilotkit/runtime-client-gql"] = path.join(
      process.cwd(),
      "src/lib/copilotkit-runtime-client-gql-shim.ts",
    );
    return webpackConfig;
  },
};

export default config;

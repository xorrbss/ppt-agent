import { register } from "node:module";

register(new URL("./node-test-resolver.mjs", import.meta.url), import.meta.url);

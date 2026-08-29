const forwardProcessOutput = (stream, target, onChunk) => {
  if (!stream) return;
  stream.on("data", (chunk) => {
    const text = chunk.toString();
    target.write(text);
    onChunk?.(text);
  });
};

export const waitForProcessReady = (name, child, readinessRegexes = []) => {
  if (readinessRegexes.length === 0) return Promise.resolve();

  return new Promise((resolve, reject) => {
    let ready = false;
    let output = "";
    const inspect = (text) => {
      if (ready) return;
      output = `${output}${text}`.slice(-8192);
      if (readinessRegexes.some((regex) => regex.test(output))) {
        ready = true;
        resolve();
      }
    };

    forwardProcessOutput(child.stdout, process.stdout, inspect);
    forwardProcessOutput(child.stderr, process.stderr, inspect);
    child.once("exit", (code) => {
      if (!ready) reject(new Error(`${name} exited before reporting ready (exit code: ${code})`));
    });
    child.once("error", (error) => {
      if (!ready) reject(error);
    });
  });
};

export const stopManagedProcesses = async (children) => {
  await Promise.all(children.map((child) => new Promise((resolve) => {
    if (child.exitCode !== null) return resolve();
    let forceTimeout;
    const finish = () => {
      clearTimeout(forceTimeout);
      resolve();
    };
    child.once("exit", () => {
      finish();
    });
    try {
      child.kill("SIGTERM");
      forceTimeout = setTimeout(() => {
        if (child.exitCode === null) child.kill("SIGKILL");
        finish();
      }, 5000);
    } catch {
      finish();
    }
  })));
};

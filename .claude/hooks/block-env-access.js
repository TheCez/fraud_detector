#!/usr/bin/env node
// PreToolUse guard: deny shell commands that touch the real .env file.
//
// .env holds live Cognee and OpenAI credentials. It is gitignored and no agent
// may read it or pipe it anywhere. .env.example is the documented, secret-free
// template and stays readable.
//
// Reads the hook payload on stdin and answers with a PreToolUse permission
// decision. Exits 0 either way so a parse failure never wedges the session.

const SECRET_FILE = /\.env(\.local)?([^.\w]|$)/;

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  raw += chunk;
});
process.stdin.on("end", () => {
  let command = "";
  try {
    command = JSON.parse(raw)?.tool_input?.command ?? "";
  } catch {
    // Unparseable payload: nothing to judge, let the normal flow continue.
    process.exit(0);
  }

  if (!SECRET_FILE.test(command)) process.exit(0);

  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason:
          "Blocked: .env holds live API credentials and is off-limits to agents. " +
          "Read .env.example for the variable names, and let the backend read the " +
          "real values at runtime.",
      },
    }),
  );
  process.exit(0);
});

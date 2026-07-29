#!/usr/bin/env node
// PreToolUse guard: deny shell commands that touch the real environment file.
//
// That file holds live Cognee and OpenAI credentials. It is gitignored and no
// agent may read it or pipe it anywhere. `.env.example` is the documented,
// secret-free template and stays readable.
//
// Default-deny: if the secret filename appears anywhere in the command, block.
// The one exemption is prose. Commit messages, PR bodies and heredocs routinely
// *describe* the file ("stop the test suite reading it"), and blocking those is
// pure friction - it blocks writing about the rule rather than breaking it. So
// message-carrying flag values and heredoc bodies are stripped before the check.
// Everything else still trips it, including `python -c "open('.env')"`: -c is
// not a message flag, so its payload is never exempt.
//
// Reads the hook payload on stdin and answers with a PreToolUse permission
// decision. Exits 0 either way so a parse failure never wedges the session.

const SECRET_FILE = /\.env(\.local)?([^.\w]|$)/;

// `<<EOF ... EOF`, `<<'EOF' ... EOF`, `<<-EOF ... EOF`. The closing tag must sit
// on its own line, which is what lets the lazy body match terminate correctly.
const HEREDOC = /<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1[\s\S]*?^\s*\2\s*$/gm;

// Quoted values of flags whose payload is human-readable text, not a path.
const MESSAGE_FLAG_VALUE =
  /(?:^|\s)(?:-m|-b|-t|--message|--body|--title|--description)(?:=|\s+)(['"])(?:\\.|(?!\1)[\s\S])*?\1/g;

function commandSkeleton(command) {
  return command.replace(HEREDOC, " <heredoc> ").replace(MESSAGE_FLAG_VALUE, " <message> ");
}

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

  if (!SECRET_FILE.test(commandSkeleton(command))) process.exit(0);

  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason:
          "Blocked: the local environment file holds live API credentials and is " +
          "off-limits to agents. Read .env.example for the variable names, and let " +
          "the backend read the real values at runtime. If you were only describing " +
          "the file in prose, put that text in a -m/--body value or a heredoc - this " +
          "guard exempts those.",
      },
    }),
  );
  process.exit(0);
});

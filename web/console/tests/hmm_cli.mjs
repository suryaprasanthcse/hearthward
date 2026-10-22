// Reads [{observations, params}, ...] as JSON on stdin and prints one trace per case. Used by tests/unit/test_console.py.
import { lambdaTraceMnats } from "../hmm.js";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const cases = JSON.parse(input);
process.stdout.write(JSON.stringify(cases.map((c) => lambdaTraceMnats(c.observations, c.params))));

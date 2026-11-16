import { readFile, writeFile } from 'node:fs/promises';
export async function buildPagesGate(source, destination) {
  const client = await readFile(new URL('./client.mjs', import.meta.url), 'utf8');
  const gate = await readFile(source, 'utf8');
  const statement = "import { quotaStatus, maintenanceResponse } from '../../services/quota_guard/client.mjs';";
  if (!gate.includes(statement)) throw new Error('Pages quota guard import missing');
  await writeFile(destination, gate.replace(statement, client));
}

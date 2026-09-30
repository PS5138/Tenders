export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const { checkConfiguration } = await import('./instrumentation.node');
    await checkConfiguration();
  }
}

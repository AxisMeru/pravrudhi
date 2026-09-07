type DismissHandler = () => void;

const stack: DismissHandler[] = [];

export function onDismiss(handler: DismissHandler): () => void {
  stack.push(handler);
  return () => {
    const i = stack.indexOf(handler);
    if (i !== -1) stack.splice(i, 1);
  };
}

export function dismissTopmost(): boolean {
  const handler = stack.pop();
  if (!handler) return false;
  handler();
  return true;
}

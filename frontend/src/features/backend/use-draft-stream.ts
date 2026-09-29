'use client';

import { useRef, useState, useEffect } from 'react';
import { backendRequest, BackendRequestError } from '@/lib/backend-api';
import { applyDraftEvent, consumeDraftStream, emptyDraft, type Answer, type Message } from '@/lib/backend-stream';

/** A single consumer for card drafts, thread replies and the development fixture. */
export function useDraftStream(workspaceId: string) {
  const [state, setState] = useState(emptyDraft);
  const [busy, setBusy] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const generation = useRef(0);
  const running = useRef(false);
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );

  async function start(
    path: string,
    body: unknown,
    recover?: () => Promise<{
      inProgress: boolean;
      result: Answer | Message | null;
    }>,
  ) {
    if (running.current) return;
    running.current = true;
    const run = ++generation.current;
    const active = () => generation.current === run;
    setBusy(true);
    setRecovering(false);
    setState(emptyDraft());
    let accepted = false;
    try {
      const response = await backendRequest(workspaceId, path, 'POST', body);
      accepted = true;
      if (!response.body) throw new Error('The backend returned no stream.');
      await consumeDraftStream(response.body, (event) => {
        if (active()) setState((previous) => applyDraftEvent(previous, event));
      });
    } catch (error) {
      // An accepted draft keeps running on the API after a connection loss. Never
      // repeat the POST: reconcile with the persisted answer/message instead.
      if ((accepted || !(error instanceof BackendRequestError)) && recover && active()) {
        setRecovering(true);
        try {
          const deadline = Date.now() + 15 * 60_000;
          while (active()) {
            await new Promise((resolve) => setTimeout(resolve, 2000));
            if (!active()) break;
            const snapshot = await recover();
            if (!snapshot.inProgress) {
              if (!snapshot.result) throw new Error('Generation ended without saving a new answer. Please try again.');
              setState((previous) =>
                applyDraftEvent(previous, {
                  type: 'done',
                  ...snapshot.result!,
                }),
              );
              return snapshot.result;
            }
            if (Date.now() >= deadline) throw new Error('Generation is still running. Reload to check its progress.');
          }
        } catch (recoveryError) {
          if (active())
            setState({
              ...emptyDraft(),
              error: (recoveryError as Error).message,
            });
        }
      } else if (error instanceof BackendRequestError) {
        // The caller handles the backend's displacement and stale-save conflicts.
        throw error;
      } else if (active()) {
        setState({ ...emptyDraft(), error: (error as Error).message });
      }
    } finally {
      running.current = false;
      if (active()) {
        setBusy(false);
        setRecovering(false);
      }
    }
  }

  return { state, busy, recovering, start };
}

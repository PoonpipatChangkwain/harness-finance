/**
 * Sending a chat message (Research + the portfolio AI Advisor). (P17) carries the
 * one-shot "ข้าม — วิเคราะห์เลย" flag of the ui-store as `skip_discovery`, and clears the
 * flag only once the message was accepted (a failed send keeps it for the retry).
 */

import { useUiStore } from "@/stores/ui-store";

import { api, type MessageAccepted, type PostMessage } from "./api";

export async function sendChatMessage(sessionId: string, body: Omit<PostMessage, "skip_discovery">): Promise<MessageAccepted> {
  const skip = useUiStore.getState().skipDiscovery;
  const accepted = await api.postMessage(sessionId, { ...body, skip_discovery: skip });
  if (skip) useUiStore.getState().setSkipDiscovery(false);
  return accepted;
}

/* eslint-disable */
/* tslint:disable */

import { FetchInit, OfflineResponse } from "./offlineClient";

declare module "./offlineClient" {
  interface OfflineFetchOptions {
    username?: string;
    skipCache?: boolean;
    fetchFn?: (
      input: RequestInfo | URL,
      init?: RequestInit
    ) => Promise<Response>;
  }

  export function fetchWithOffline(
    input: RequestInfo | URL,
    init?: RequestInit,
    options?: OfflineFetchOptions
  ): Promise<Response>;

  export function queueRequest(
    method: string,
    url: string,
    body: BodyInit | null,
    headers?: Record<string, string>
  ): Promise<Response>;

  export async function flushQueue(): Promise<{ synced: number; failed: number }>;

  export function isOffline(): boolean;

  export function onOnlineChange(listener: (isOnline: boolean) => void): () => void;

  export async function isDuplicate(
    method: string,
    url: string,
    body: BodyInit | null
  ): Promise<boolean>;
}

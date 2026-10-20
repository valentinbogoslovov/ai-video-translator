"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Segment } from "@/lib/types";

export function useSegments(projectId: string) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(true);

  const fetch = () => {
    api
      .listSegments(projectId)
      .then(setSegments)
      .finally(() => setLoading(false));
  };

  useEffect(fetch, [projectId]);

  return { segments, loading, refetch: fetch };
}

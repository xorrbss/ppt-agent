class ImmutableBufferMap implements ReadonlyMap<string, Buffer> {
  readonly #entries: ReadonlyMap<string, Buffer>;

  constructor(entries: ReadonlyMap<string, Buffer>) {
    const snapshot = new Map<string, Buffer>();
    for (const [name, bytes] of entries) snapshot.set(name, Buffer.from(bytes));
    this.#entries = snapshot;
  }

  get size(): number {
    return this.#entries.size;
  }

  has(key: string): boolean {
    return this.#entries.has(key);
  }

  get(key: string): Buffer | undefined {
    const bytes = this.#entries.get(key);
    return bytes === undefined ? undefined : Buffer.from(bytes);
  }

  *entries(): MapIterator<[string, Buffer]> {
    for (const [name, bytes] of this.#entries) yield [name, Buffer.from(bytes)];
  }

  *keys(): MapIterator<string> {
    yield* this.#entries.keys();
  }

  *values(): MapIterator<Buffer> {
    for (const bytes of this.#entries.values()) yield Buffer.from(bytes);
  }

  forEach(
    callbackfn: (value: Buffer, key: string, map: ReadonlyMap<string, Buffer>) => void,
    thisArg?: unknown,
  ): void {
    for (const [name, bytes] of this.#entries) {
      callbackfn.call(thisArg, Buffer.from(bytes), name, this);
    }
  }

  [Symbol.iterator](): MapIterator<[string, Buffer]> {
    return this.entries();
  }

  get [Symbol.toStringTag](): string {
    return "ImmutableBufferMap";
  }
}

/** A defensive immutable snapshot of package bytes. */
export function immutablePackageEntries(
  entries: ReadonlyMap<string, Buffer>,
): ReadonlyMap<string, Buffer> {
  return new ImmutableBufferMap(entries);
}

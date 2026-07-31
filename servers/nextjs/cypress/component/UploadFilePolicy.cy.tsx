import { isAllowedFile } from "@/app/(presentation-generator)/upload/components/SupportingDoc";

describe("supporting document file policy", () => {
  it("treats a named file extension as authoritative", () => {
    expect(
      isAllowedFile(
        new File(["not a document"], "unsafe.exe", { type: "text/plain" })
      )
    ).to.equal(false);
    expect(
      isAllowedFile(
        new File(["# Notes"], "notes.md", { type: "application/octet-stream" })
      )
    ).to.equal(true);
  });

  it("uses MIME only when the file has no extension", () => {
    expect(
      isAllowedFile(new File(["plain text"], "README", { type: "text/plain" }))
    ).to.equal(true);
    expect(
      isAllowedFile(
        new File(["binary"], "payload", { type: "application/octet-stream" })
      )
    ).to.equal(false);
  });
});

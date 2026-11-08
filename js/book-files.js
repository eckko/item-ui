/*
 * book-files.js
 * Says where a book's own encrypted files are when they do not sit next to
 * the page (the library keeps them in a Worker, one folder per book).
 *
 * Two optional <meta> tags on the page:
 *
 *   <meta name="book-base" content="https://worker.example/p/<id>/">
 *   <meta name="book-version" content="3">
 *
 * RecallQuizBookFiles.address("data/manifest.enc") then gives
 * "<book-base>data/manifest.enc?v=3". The version is the card's "v": a
 * changed file gets a new address, so a cached copy is never shown for a
 * republished book. Without the tags the address is the path unchanged.
 *
 * A classic script with no dependencies: load it before the files that
 * fetch the book (early-questions.js, reader/app.js, media-store.js).
 */
(function setUpBookFiles() {
  "use strict";

  /**
   * @param {string} name  the meta tag's name
   * @returns {string} its content, or ""
   */
  function metaContent(name) {
    const tag = document.querySelector('meta[name="' + name + '"]');
    return (tag && tag.content) || "";
  }

  /**
   * @param {string} path  a file of the book, such as "quiz/q/ab.enc"
   * @returns {string} the address to fetch it from
   */
  function address(path) {
    const version = metaContent("book-version");
    return metaContent("book-base") + path +
      (version ? "?v=" + encodeURIComponent(version) : "");
  }

  window.RecallQuizBookFiles = { address: address };
})();

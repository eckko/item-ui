/*
 * encrypted-book.js
 * On an encrypted book site (template v3) gives the picture store the
 * subject key that js/unlock.js already has, so the quiz opens its
 * pictures with the same key as everything else. On any other site it
 * does nothing.
 *
 * The key is only in memory. A site without it shows the book's own plain
 * pictures (see js/media-store.js).
 */
(function useSubjectKeyForPictures(quiz) {
  "use strict";

  /**
   * @returns {Promise<string|null>} the subject key in base64
   */
  function provideSubjectKey() {
    return Promise.resolve(window.RecallQuizSubjectKeyBase64 || null);
  }

  if (window.RecallQuizSubjectKeyBase64 && quiz.media) {
    quiz.media.keyLocation = "the subject key";
    quiz.media.setKeyProvider(provideSubjectKey);
  }
})((window.RecallQuiz = window.RecallQuiz || {}));

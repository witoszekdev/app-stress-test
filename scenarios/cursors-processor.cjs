module.exports = {
  // Initialize the cursors array and the nextCursor variable.
  initCursors: function (context, events, done) {
    context.vars.allCursors = [];
    context.vars.nextCursor = null;
    return done();
  },
  // Append the fetched cursor value to the array (if it exists and is unique).
  appendCursor: function (context, events, done) {
    if (context.vars.nextCursor && !context.vars.allCursors.includes(context.vars.nextCursor)) {
      context.vars.allCursors.push(context.vars.nextCursor);
    }
    return done();
  }
};

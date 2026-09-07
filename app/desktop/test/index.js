// Support the explicit `node --test test/` invocation on Node versions that
// resolve directory arguments as modules instead of discovering their files.
//
// Every test file must be listed here. Two were added and not listed, so they sat in the directory and never
// ran: the suite reported the same count as before and the tasks that wrote them passed their own validation
// on the strength of tests nobody executed. A file added without a line here is a file that does nothing.
require('./core.test.js');
require('./integration.test.js');
require('./product.test.js');
require('./updates.test.js');

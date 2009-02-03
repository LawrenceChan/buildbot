# -*- test-case-name: buildbot.test.test_transfer -*-

import os
from stat import ST_MODE
from twisted.trial import unittest
from buildbot.process.buildstep import WithProperties
from buildbot.steps.transfer import FileUpload, FileDownload
from buildbot.test.runutils import StepTester
from buildbot.status.builder import SUCCESS, FAILURE

# these steps pass a pb.Referenceable inside their arguments, so we have to
# catch and wrap them. If the LocalAsRemote wrapper were a proper membrane,
# we wouldn't have to do this.

class Upload(StepTester, unittest.TestCase):

    def filterArgs(self, args):
        if "writer" in args:
            args["writer"] = self.wrap(args["writer"])
        return args

    def testSuccess(self):
        self.slavebase = "Upload.testSuccess.slave"
        self.masterbase = "Upload.testSuccess.master"
        sb = self.makeSlaveBuilder()
        os.mkdir(os.path.join(self.slavebase, self.slavebuilderbase,
                              "build"))
        # the buildmaster normally runs chdir'ed into masterbase, so uploaded
        # files will appear there. Under trial, we're chdir'ed into
        # _trial_temp instead, so use a different masterdest= to keep the
        # uploaded file in a test-local directory
        masterdest = os.path.join(self.masterbase, "dest.text")
        step = self.makeStep(FileUpload,
                             slavesrc="source.txt",
                             masterdest=masterdest)
        slavesrc = os.path.join(self.slavebase,
                                self.slavebuilderbase,
                                "build",
                                "source.txt")
        contents = "this is the source file\n" * 1000
        open(slavesrc, "w").write(contents)
        f = open(masterdest, "w")
        f.write("overwrite me\n")
        f.close()

        d = self.runStep(step)
        def _checkUpload(results):
            step_status = step.step_status
            #l = step_status.getLogs()
            #if l:
            #    logtext = l[0].getText()
            #    print logtext
            self.failUnlessEqual(results, SUCCESS)
            self.failUnless(os.path.exists(masterdest))
            masterdest_contents = open(masterdest, "r").read()
            self.failUnlessEqual(masterdest_contents, contents)
        d.addCallback(_checkUpload)
        return d

    def testMaxsize(self):
        self.slavebase = "Upload.testMaxsize.slave"
        self.masterbase = "Upload.testMaxsize.master"
        sb = self.makeSlaveBuilder()
        os.mkdir(os.path.join(self.slavebase, self.slavebuilderbase,
                              "build"))
        masterdest = os.path.join(self.masterbase, "dest2.text")
        step = self.makeStep(FileUpload,
                             slavesrc="source.txt",
                             masterdest=masterdest,
                             maxsize=12345)
        slavesrc = os.path.join(self.slavebase,
                                self.slavebuilderbase,
                                "build",
                                "source.txt")
        contents = "this is the source file\n" * 1000
        open(slavesrc, "w").write(contents)
        f = open(masterdest, "w")
        f.write("overwrite me\n")
        f.close()

        d = self.runStep(step)
        def _checkUpload(results):
            step_status = step.step_status
            #l = step_status.getLogs()
            #if l:
            #    logtext = l[0].getText()
            #    print logtext
            self.failUnlessEqual(results, FAILURE)
            self.failUnless(os.path.exists(masterdest))
            masterdest_contents = open(masterdest, "r").read()
            self.failUnlessEqual(len(masterdest_contents), 12345)
            self.failUnlessEqual(masterdest_contents, contents[:12345])
        d.addCallback(_checkUpload)
        return d

    def testMode(self):
        self.slavebase = "Upload.testMode.slave"
        self.masterbase = "Upload.testMode.master"
        sb = self.makeSlaveBuilder()
        os.mkdir(os.path.join(self.slavebase, self.slavebuilderbase,
                              "build"))
        masterdest = os.path.join(self.masterbase, "dest3.text")
        step = self.makeStep(FileUpload,
                             slavesrc="source.txt",
                             masterdest=masterdest,
                             mode=0755)
        slavesrc = os.path.join(self.slavebase,
                                self.slavebuilderbase,
                                "build",
                                "source.txt")
        contents = "this is the source file\n"
        open(slavesrc, "w").write(contents)
        f = open(masterdest, "w")
        f.write("overwrite me\n")
        f.close()

        d = self.runStep(step)
        def _checkUpload(results):
            step_status = step.step_status
            #l = step_status.getLogs()
            #if l:
            #    logtext = l[0].getText()
            #    print logtext
            self.failUnlessEqual(results, SUCCESS)
            self.failUnless(os.path.exists(masterdest))
            masterdest_contents = open(masterdest, "r").read()
            self.failUnlessEqual(masterdest_contents, contents)
            # and with 0777 to ignore sticky bits
            dest_mode = os.stat(masterdest)[ST_MODE] & 0777
            self.failUnlessEqual(dest_mode, 0755,
                                 "target mode was %o, we wanted %o" %
                                 (dest_mode, 0755))
        d.addCallback(_checkUpload)
        return d

    def testMissingFile(self):
        self.slavebase = "Upload.testMissingFile.slave"
        self.masterbase = "Upload.testMissingFile.master"
        sb = self.makeSlaveBuilder()
        step = self.makeStep(FileUpload,
                             slavesrc="MISSING.txt",
                             masterdest="dest.txt")
        masterdest = os.path.join(self.masterbase, "dest4.txt")

        d = self.runStep(step)
        def _checkUpload(results):
            step_status = step.step_status
            self.failUnlessEqual(results, FAILURE)
            self.failIf(os.path.exists(masterdest))
            l = step_status.getLogs()
            logtext = l[0].getText().strip()
            self.failUnless(logtext.startswith("Cannot open file"))
            self.failUnless(logtext.endswith("for upload"))
        d.addCallback(_checkUpload)
        return d

    def testLotsOfBlocks(self):
        self.slavebase = "Upload.testLotsOfBlocks.slave"
        self.masterbase = "Upload.testLotsOfBlocks.master"
        sb = self.makeSlaveBuilder()
        os.mkdir(os.path.join(self.slavebase, self.slavebuilderbase,
                              "build"))
        # the buildmaster normally runs chdir'ed into masterbase, so uploaded
        # files will appear there. Under trial, we're chdir'ed into
        # _trial_temp instead, so use a different masterdest= to keep the
        # uploaded file in a test-local directory
        masterdest = os.path.join(self.masterbase, "dest.text")
        step = self.makeStep(FileUpload,
                             slavesrc="source.txt",
                             masterdest=masterdest,
                             blocksize=15)
        slavesrc = os.path.join(self.slavebase,
                                self.slavebuilderbase,
                                "build",
                                "source.txt")
        contents = "".join(["this is the source file #%d\n" % i
                            for i in range(1000)])
        open(slavesrc, "w").write(contents)
        f = open(masterdest, "w")
        f.write("overwrite me\n")
        f.close()

        d = self.runStep(step)
        def _checkUpload(results):
            step_status = step.step_status
            #l = step_status.getLogs()
            #if l:
            #    logtext = l[0].getText()
            #    print logtext
            self.failUnlessEqual(results, SUCCESS)
            self.failUnless(os.path.exists(masterdest))
            masterdest_contents = open(masterdest, "r").read()
            self.failUnlessEqual(masterdest_contents, contents)
        d.addCallback(_checkUpload)
        return d

    def testWorkdir(self):
        self.slavebase = "Upload.testWorkdir.slave"
        self.masterbase = "Upload.testWorkdir.master"
        sb = self.makeSlaveBuilder()

        self.workdir = "mybuild"        # override default in StepTest
        full_workdir = os.path.join(
            self.slavebase, self.slavebuilderbase, self.workdir)
        os.mkdir(full_workdir)

        masterdest = os.path.join(self.masterbase, "dest.txt")
        
        step = self.makeStep(FileUpload,
                             slavesrc="source.txt",
                             masterdest=masterdest)

        # Testing that the FileUpload's workdir is set when makeStep()
        # calls setDefaultWorkdir() is actually enough; carrying on and
        # making sure the upload actually succeeds is pure gravy.
        self.failUnlessEqual(self.workdir, step.workdir)

        slavesrc = os.path.join(full_workdir, "source.txt")
        open(slavesrc, "w").write("upload me\n")

        def _checkUpload(results):
            self.failUnlessEqual(results, SUCCESS)
            self.failUnless(os.path.isfile(masterdest))

        d = self.runStep(step)
        d.addCallback(_checkUpload)
        return d

    def testWithProperties(self):
        # test that workdir can be a WithProperties object
        self.slavebase = "Upload.testWithProperties.slave"
        self.masterbase = "Upload.testWithProperties.master"
        sb = self.makeSlaveBuilder()

        step = self.makeStep(FileUpload,
                             slavesrc="src.txt",
                             masterdest="dest.txt")
        step.workdir = WithProperties("build.%s", "buildnumber")

        self.failUnlessEqual(step._getWorkdir(), "build.1")

class Download(StepTester, unittest.TestCase):

    def filterArgs(self, args):
        if "reader" in args:
            args["reader"] = self.wrap(args["reader"])
        return args

    def testSuccess(self):
        self.slavebase = "Download.testSuccess.slave"
        self.masterbase = "Download.testSuccess.master"
        sb = self.makeSlaveBuilder()
        os.mkdir(os.path.join(self.slavebase, self.slavebuilderbase,
                              "build"))
        mastersrc = os.path.join(self.masterbase, "source.text")
        slavedest = os.path.join(self.slavebase,
                                 self.slavebuilderbase,
                                 "build",
                                 "dest.txt")
        step = self.makeStep(FileDownload,
                             mastersrc=mastersrc,
                             slavedest="dest.txt")
        contents = "this is the source file\n" * 1000  # 24kb, so two blocks
        open(mastersrc, "w").write(contents)
        f = open(slavedest, "w")
        f.write("overwrite me\n")
        f.close()

        d = self.runStep(step)
        def _checkDownload(results):
            step_status = step.step_status
            self.failUnlessEqual(results, SUCCESS)
            self.failUnless(os.path.exists(slavedest))
            slavedest_contents = open(slavedest, "r").read()
            self.failUnlessEqual(slavedest_contents, contents)
        d.addCallback(_checkDownload)
        return d

    def testMaxsize(self):
        self.slavebase = "Download.testMaxsize.slave"
        self.masterbase = "Download.testMaxsize.master"
        sb = self.makeSlaveBuilder()
        os.mkdir(os.path.join(self.slavebase, self.slavebuilderbase,
                              "build"))
        mastersrc = os.path.join(self.masterbase, "source.text")
        slavedest = os.path.join(self.slavebase,
                                 self.slavebuilderbase,
                                 "build",
                                 "dest.txt")
        step = self.makeStep(FileDownload,
                             mastersrc=mastersrc,
                             slavedest="dest.txt",
                             maxsize=12345)
        contents = "this is the source file\n" * 1000  # 24kb, so two blocks
        open(mastersrc, "w").write(contents)
        f = open(slavedest, "w")
        f.write("overwrite me\n")
        f.close()

        d = self.runStep(step)
        def _checkDownload(results):
            step_status = step.step_status
            # the file should be truncated, and the step a FAILURE
            self.failUnlessEqual(results, FAILURE)
            self.failUnless(os.path.exists(slavedest))
            slavedest_contents = open(slavedest, "r").read()
            self.failUnlessEqual(len(slavedest_contents), 12345)
            self.failUnlessEqual(slavedest_contents, contents[:12345])
        d.addCallback(_checkDownload)
        return d

    def testMode(self):
        self.slavebase = "Download.testMode.slave"
        self.masterbase = "Download.testMode.master"
        sb = self.makeSlaveBuilder()
        os.mkdir(os.path.join(self.slavebase, self.slavebuilderbase,
                              "build"))
        mastersrc = os.path.join(self.masterbase, "source.text")
        slavedest = os.path.join(self.slavebase,
                                 self.slavebuilderbase,
                                 "build",
                                 "dest.txt")
        step = self.makeStep(FileDownload,
                             mastersrc=mastersrc,
                             slavedest="dest.txt",
                             mode=0755)
        contents = "this is the source file\n"
        open(mastersrc, "w").write(contents)
        f = open(slavedest, "w")
        f.write("overwrite me\n")
        f.close()

        d = self.runStep(step)
        def _checkDownload(results):
            step_status = step.step_status
            self.failUnlessEqual(results, SUCCESS)
            self.failUnless(os.path.exists(slavedest))
            slavedest_contents = open(slavedest, "r").read()
            self.failUnlessEqual(slavedest_contents, contents)
            # and with 0777 to ignore sticky bits
            dest_mode = os.stat(slavedest)[ST_MODE] & 0777
            self.failUnlessEqual(dest_mode, 0755,
                                 "target mode was %o, we wanted %o" %
                                 (dest_mode, 0755))
        d.addCallback(_checkDownload)
        return d

    def testMissingFile(self):
        self.slavebase = "Download.testMissingFile.slave"
        self.masterbase = "Download.testMissingFile.master"
        sb = self.makeSlaveBuilder()
        os.mkdir(os.path.join(self.slavebase, self.slavebuilderbase,
                              "build"))
        mastersrc = os.path.join(self.masterbase, "MISSING.text")
        slavedest = os.path.join(self.slavebase,
                                 self.slavebuilderbase,
                                 "build",
                                 "dest.txt")
        step = self.makeStep(FileDownload,
                             mastersrc=mastersrc,
                             slavedest="dest.txt")

        d = self.runStep(step)
        def _checkDownload(results):
            step_status = step.step_status
            self.failUnlessEqual(results, FAILURE)
            self.failIf(os.path.exists(slavedest))
            l = step_status.getLogs()
            logtext = l[0].getText().strip()
            self.failUnless(logtext.endswith(" not available at master"))
        d.addCallbacks(_checkDownload)

        return d

    def testLotsOfBlocks(self):
        self.slavebase = "Download.testLotsOfBlocks.slave"
        self.masterbase = "Download.testLotsOfBlocks.master"
        sb = self.makeSlaveBuilder()
        os.mkdir(os.path.join(self.slavebase, self.slavebuilderbase,
                              "build"))
        mastersrc = os.path.join(self.masterbase, "source.text")
        slavedest = os.path.join(self.slavebase,
                                 self.slavebuilderbase,
                                 "build",
                                 "dest.txt")
        step = self.makeStep(FileDownload,
                             mastersrc=mastersrc,
                             slavedest="dest.txt",
                             blocksize=15)
        contents = "".join(["this is the source file #%d\n" % i
                            for i in range(1000)])
        open(mastersrc, "w").write(contents)
        f = open(slavedest, "w")
        f.write("overwrite me\n")
        f.close()

        d = self.runStep(step)
        def _checkDownload(results):
            step_status = step.step_status
            self.failUnlessEqual(results, SUCCESS)
            self.failUnless(os.path.exists(slavedest))
            slavedest_contents = open(slavedest, "r").read()
            self.failUnlessEqual(slavedest_contents, contents)
        d.addCallback(_checkDownload)
        return d

    def testWorkdir(self):
        self.slavebase = "Download.testWorkdir.slave"
        self.masterbase = "Download.testWorkdir.master"
        sb = self.makeSlaveBuilder()

        # As in Upload.testWorkdir(), it's enough to test that makeStep()'s
        # call of setDefaultWorkdir() actually sets step.workdir.
        self.workdir = "mybuild"
        step = self.makeStep(FileDownload,
                             mastersrc="foo",
                             slavedest="foo")
        self.failUnlessEqual(step.workdir, self.workdir)

    def testWithProperties(self):
        # test that workdir can be a WithProperties object
        self.slavebase = "Download.testWithProperties.slave"
        self.masterbase = "Download.testWithProperties.master"
        sb = self.makeSlaveBuilder()

        step = self.makeStep(FileDownload,
                             mastersrc="src.txt",
                             slavedest="dest.txt")
        step.workdir = WithProperties("build.%s", "buildnumber")

        self.failUnlessEqual(step._getWorkdir(), "build.1")

        

# TODO:
#  test relative paths, ~/paths
#   need to implement expanduser() for slave-side
#  test error message when master-side file is in a missing directory
#  remove workdir= default?


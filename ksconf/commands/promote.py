""" SUBCOMMAND:  ksconf promote --target=<CONF> <CONF>

Usage example:  Promote local props changes (made via the UI) to the 'default' folder

    ksconf --target=default/props.conf local/props.conf

"""

from __future__ import absolute_import, unicode_literals

import os
import shutil
from copy import deepcopy

from six.moves import input

from ksconf.commands import ConfDirProxy
from ksconf.commands import KsconfCmd, dedent, ConfFileType
from ksconf.conf.delta import compare_cfgs, DIFF_OP_DELETE, summarize_cfg_diffs, show_diff, \
    DIFF_OP_EQUAL, DiffStanza
from ksconf.conf.merge import merge_conf_dicts
from ksconf.conf.parser import PARSECONF_STRICT_NC, PARSECONF_STRICT
from ksconf.consts import EXIT_CODE_FAILED_SAFETY_CHECK, EXIT_CODE_NOTHING_TO_DO, \
    EXIT_CODE_USER_QUIT, EXIT_CODE_EXTERNAL_FILE_EDIT
from ksconf.util.completers import conf_files_completer
from ksconf.util.file import _samefile, file_fingerprint

""" Possible behaviors.... thinking through what CLI options make the most sense...

 Things we may want to control:

     Q: What mode of operation?
         1.)  Automatic (merge all)
         2.)  Interactive (user guided / sub-shell)
         3.)  Batch mode:  CLI driven based on a stanza or key using either a name or pattern to
              select which content should be integrated.

     Q: What happens to the original?
         1.)  Updated
           a.)  Only remove source content that has been integrated into the target.
           b.)  Let the user pick
         2.)  Preserved  (Dry-run, or don't delete the original mode);  if output is stdout.
         3.)  Remove
           a.)  Only if all content was integrated.
           b.)  If user chose to discard entry.
           c.)  Always (--always-remove)
     Q: What to do with discarded content?
         1.)  Remove from the original (destructive)
         2.)  Place in a "discard" file.  (Allow the user to select the location of the file.)
         3.)  Automatically backup discards to a internal store, and/or log.  (More difficult to
              recover, but content is always logged/recoverable with some effort.)


 Interactive approach:

     3 action options:
         Integrate/Accept: Move content from the source to the target  (e.g., local to default)
         Reject/Remove:    Discard content from the source; destructive (e.g., rm local setting)
         Skip/Keep:        Don't push to target or remove from source (no change)

"""



class PromoteCmd(KsconfCmd):
    help = dedent("""\
    Promote .conf settings from one file into another either in batch mode (all
    changes) or interactively allowing the user to pick which stanzas and keys to
    integrate.

    Changes made via the UI (stored in the local folder) can be promoted (moved) to
    a version-controlled directory.
    """)
    description = dedent("""\
    Propagate .conf settings applied in one file to another.  Typically this is used
    to take local changes made via the UI and push them into a default (or
    default.d/) location.

    NOTICE:  By default, changes are *MOVED*, not just copied.

    Promote has two different modes:  batch and interactive.  In batch mode all
    changes are applied automatically and the (now empty) source file is removed.
    In interactive mode the user is prompted to pick which stanzas and keys to
    integrate.  This can be used to push  changes made via the UI, which are stored
    in a 'local' file, to the version-controlled 'default' file.  Note that the
    normal operation moves changes from the SOURCE file to the TARGET, updating both
    files in the process.  But it's also possible to preserve the local file, if
    desired.

    If either the source file or target file is modified while a promotion is under
    progress, changes will be aborted.  And any custom selections you made will be
    lost.  (This needs improvement.)
    """)
    format = "manual"

    def register_args(self, parser):
        parser.set_defaults(mode="ask")
        parser.add_argument("source", metavar="SOURCE",
                            type=ConfFileType("r+", "load", parse_profile=PARSECONF_STRICT_NC),
                            help="""The source configuration file to pull changes from.
                                 (Typically the 'local' conf file)"""
                            ).completer = conf_files_completer
        parser.add_argument("target", metavar="TARGET",
                            type=ConfFileType("r+", "none", accept_dir=True,
                                              parse_profile=PARSECONF_STRICT), help="""
            Configuration file or directory to push the changes into.
            (Typically the 'default' folder)
            As a shortcut, a directory is given, then it's assumed that the same basename is
            used for both SOURCE and TARGET.
            In fact, if different basename as provided, a warning is issued."""
                            ).completer = conf_files_completer
        grp1 = parser.add_mutually_exclusive_group()
        grp1.add_argument("--batch", "-b", action="store_const",
                          dest="mode", const="batch", help="""
            Use batch mode where all configuration settings are automatically promoted.
            All changes are removed from source and applied to target.
            The source file will be removed, unless
            '--keep-empty' is used.""")
        grp1.add_argument("--interactive", "-i",
                          action="store_const",
                          dest="mode", const="interactive", help="""
            Enable interactive mode where the user will be prompted to approve
            the promotion of specific stanzas and keys.
            The user will be able to apply, skip, or edit the changes being promoted.
            (This functionality was inspired by 'git add --patch').""")
        parser.add_argument("--force", "-f",
                            action="store_true", default=False,
                            help="Disable safety checks.")
        parser.add_argument("--keep", "-k",
                            action="store_true", default=False, help="""
            Keep conf settings in the source file.
            All changes will be copied into the target file instead of being moved there.
            This is typically a bad idea since local always overrides default.""")
        parser.add_argument("--keep-empty",
                            action="store_true", default=False, help="""
            Keep the source file, even if after the settings promotions the file has no content.
            By default, SOURCE will be removed after all content has been moved into TARGET.
            Splunk will re-create any necessary local files on the fly.""")

    def run(self, args):
        if isinstance(args.target, ConfDirProxy):
            # If a directory is given instead of a target file, then assume the source filename
            # and target filename are the same.
            args.target = args.target.get_file(os.path.basename(args.source.name))

        if not os.path.isfile(args.target.name):
            self.stdout.write("Target file {} does not exist.  Moving source file {} to the target."
                              .format(args.target.name, args.source.name))
            # For windows:  Close out any open file descriptors first
            args.target.close()
            args.source.close()
            if args.keep:
                shutil.copy2(args.source.name, args.target.name)
            else:
                shutil.move(args.source.name, args.target.name)
            return

        # If src/dest are the same, then the file ends up being deleted.  Whoops!
        if _samefile(args.source.name, args.target.name):
            self.stderr.write("Aborting.  SOURCE and TARGET are the same file!\n")
            return EXIT_CODE_FAILED_SAFETY_CHECK

        fp_source = file_fingerprint(args.source.name)
        fp_target = file_fingerprint(args.target.name)

        # Todo: Add a safety check prevent accidental merge of unrelated files.
        # Scenario: promote local/props.conf into default/transforms.conf
        # Possible check (1) Are basenames are different?  (props.conf vs transforms.conf)
        # Possible check (2) Are there key's in common? (DEST_KEY vs REPORT)
        # Using #1 for now, consider if there's value in #2
        bn_source = os.path.basename(args.source.name)
        bn_target = os.path.basename(args.target.name)
        if bn_source.endswith(".meta") and bn_target.endswith(".meta"):
            # Allow local.meta -> default.meta without --force or a warning message
            pass
        elif bn_source != bn_target:
            # Todo: Allow for interactive prompting when in interactive but not force mode.
            if args.force:
                self.stderr.write(
                    "Promoting content across conf file types ({0} --> {1}) because the "
                    "'--force' CLI option was set.\n".format(bn_source, bn_target))
            else:
                self.stderr.write(
                    "Refusing to promote content between different types of configuration "
                    "files.  {0} --> {1}  If this is intentional, override this safety"
                    "check with '--force'\n".format(bn_source, bn_target))
                return EXIT_CODE_FAILED_SAFETY_CHECK

        # Todo:  Preserve comments in the TARGET file.  Worry with promoting of comments later...
        # Parse all config files
        cfg_src = args.source.data
        cfg_tgt = args.target.data

        if not cfg_src:
            self.stderr.write("No settings in {}.  Nothing to promote.\n".format(args.source.name))
            return EXIT_CODE_NOTHING_TO_DO

        if args.mode == "ask":
            # Show a summary of how many new stanzas would be copied across; how many key changes.
            # ANd either accept all (batch) or pick selectively (batch)
            delta = compare_cfgs(cfg_tgt, cfg_src, allow_level0=False)
            delta = [op for op in delta if op.tag != DIFF_OP_DELETE]
            summarize_cfg_diffs(delta, self.stderr)

            while True:
                resp = input("Would you like to apply ALL changes?  (y/n/d/q)")
                resp = resp[:1].lower()
                if resp == 'q':
                    return EXIT_CODE_USER_QUIT
                elif resp == 'd':
                    show_diff(self.stdout, delta, headers=(args.source.name, args.target.name))
                elif resp == 'y':
                    args.mode = "batch"
                    break
                elif resp == 'n':
                    args.mode = "interactive"
                    break

        if args.mode == "interactive":
            (cfg_final_src, cfg_final_tgt) = self._do_promote_interactive(cfg_src, cfg_tgt, args)
        else:
            (cfg_final_src, cfg_final_tgt) = self._do_promote_automatic(cfg_src, cfg_tgt, args)

        # Minimize race condition:  Do file mtime/hash check here.  Abort on external change.
        # Todo: Eventually use temporary files and atomic renames to further minimize the risk
        # Todo: Make backup '.bak' files (user configurable)
        # Todo: Avoid rewriting files if NO changes were made. (preserve prior backups)
        # Todo: Restore file modes and such

        if file_fingerprint(args.source.name, fp_source):
            self.stderr.write("Aborting!  External source file changed: {0}\n".
                              format(args.source.name))
            return EXIT_CODE_EXTERNAL_FILE_EDIT
        if file_fingerprint(args.target.name, fp_target):
            self.stderr.write("Aborting!  External target file changed: {0}\n".
                              format(args.target.name))
            return EXIT_CODE_EXTERNAL_FILE_EDIT
        # Reminder:  conf entries are being removed from source and promoted into target
        args.target.dump(cfg_final_tgt)
        if not args.keep:
            # If --keep is set, we never touch the source file.
            if cfg_final_src:
                args.source.dump(cfg_final_src)
            else:
                # Config file is empty.  Should we write an empty file, or remove it?
                if args.keep_empty:
                    args.source.dump(cfg_final_src)
                else:
                    args.source.unlink()

    @staticmethod
    def _do_promote_automatic(cfg_src, cfg_tgt, args):
        # Promote ALL entries;  simply, isn't it...  ;-)
        final_cfg = merge_conf_dicts(cfg_tgt, cfg_src)
        return ({}, final_cfg)


    def _do_promote_interactive(self, cfg_src, cfg_tgt, args):
        ''' Interactively "promote" settings from one configuration file into another

        Model after git's "patch" mode, from git docs:

        This lets you choose one path out of a status like selection. After choosing the path, it
        presents the diff between the index and the working tree file and asks you if you want to
        stage the change of each hunk. You can select one of the following options and type return:

           y - stage this hunk
           n - do not stage this hunk
           q - quit; do not stage this hunk or any of the remaining ones
           a - stage this hunk and all later hunks in the file
           d - do not stage this hunk or any of the later hunks in the file
           g - select a hunk to go to
           / - search for a hunk matching the given regex
           j - leave this hunk undecided, see next undecided hunk
           J - leave this hunk undecided, see next hunk
           k - leave this hunk undecided, see previous undecided hunk
           K - leave this hunk undecided, see previous hunk
           s - split the current hunk into smaller hunks
           e - manually edit the current hunk
           ? - print help


        Note:  In git's "edit" mode you are literally editing a patch file, so you can modify both
        the working tree file as well as the file that's being staged.  While this is nifty, as
        git's own documentation points out (in other places), that "some changes may have confusing
        results".  Therefore, it probably makes sense to limit what the user can edit.

        ============================================================================================

        Options we may be able to support:

           Pri k   Description
           --- -   -----------
           [1] y - stage this section or key
           [1] n - do not stage this section or key
           [1] q - quit; do not stage this or any of the remaining sections or keys
           [2] a - stage this section or key and all later sections in the file
           [2] d - do not stage this section or key or any of the later section or key in the file
           [1] s - split the section into individual keys
           [3] e - edit the current section or key
           [2] ? - print help

        Q:  Is it less confusing to the user to adopt the 'local' and 'default' paradigm here?
        Even though we know that change promotions will not *always* be between default and local.
        (We can and should assume some familiarity with Splunk conf, less so than familiarity
        with git lingo.)
        '''

        def prompt_yes_no(prompt):
            while True:
                r = input(prompt + " (y/n)")
                if r.lower().startswith("y"):
                    return True
                elif r.lower().startswith("n"):
                    return False

        out_src = deepcopy(cfg_src)
        out_cfg = deepcopy(cfg_tgt)
        ###  Todo:  IMPLEMENT A MANUAL MERGE/DIFF HERE:
        # What ever is migrated, move it OUT of cfg_src, and into cfg_tgt

        diff = compare_cfgs(cfg_tgt, cfg_src, allow_level0=False)
        for op in diff:
            if op.tag == DIFF_OP_DELETE:
                # This is normal.   Not all default entries will be updated in local.
                continue
            elif op.tag == DIFF_OP_EQUAL:
                # Q:  Should we simply remove everything from the source file that already lines
                #     up with the target?  (Probably?)  For now just skip...
                if prompt_yes_no("Remove matching entry {0}  ".format(op.location)):
                    if isinstance(op.location, DiffStanza):
                        del out_src[op.location.stanza]
                    else:
                        del out_src[op.location.stanza][op.location.key]
            else:
                '''
                self.stderr.write("Found change:  <{0}> {1!r}\n-{2!r}\n+{3!r}\n\n\n"
                    .format(op.tag, op.location, op.b, op.a))
                '''
                if isinstance(op.location, DiffStanza):
                    # Move entire stanza
                    show_diff(self.stdout, [op])
                    if prompt_yes_no("Apply  [{0}]".format(op.location.stanza)):
                        out_cfg[op.location.stanza] = op.a
                        del out_src[op.location.stanza]
                else:
                    show_diff(self.stdout, [op])
                    if prompt_yes_no("Apply [{0}] {1}".format(op.location.stanza, op.location.key)):
                        # Move key
                        out_cfg[op.location.stanza][op.location.key] = op.a
                        del out_src[op.location.stanza][op.location.key]
                        # If last remaining key in the src stanza?  Then delete the entire stanza
                        if not out_src[op.location.stanza]:
                            del out_src[op.location.stanza]
        return (out_src, out_cfg)

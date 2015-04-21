# HG changeset patch
# User Garvan Keeley <gkeeley@mozilla.com>
# Parent  930932c7eedd26ad880bd8d904a07422bb07a339
Bug 1063329 - Part 3, add xcode_packend.py, the project generator

diff --git a/python/mozbuild/mozbuild/backend/xcode_backend.py b/python/mozbuild/mozbuild/backend/xcode_backend.py
new file mode 100644
--- /dev/null
+++ b/python/mozbuild/mozbuild/backend/xcode_backend.py
@@ -0,0 +1,284 @@
+# This Source Code Form is subject to the terms of the Mozilla Public
+# License, v. 2.0. If a copy of the MPL was not distributed with this
+# file, You can obtain one at http://mozilla.org/MPL/2.0/.
+
+import os
+import types
+import shutil
+import re
+from .common import CommonBackend
+from ..frontend.data import (
+    Defines, Exports, Sources, GeneratedSources, GeneratedInclude, UnifiedSources, LocalInclude, HostSources,
+    DirectoryTraversal, PerSourceFlag, VariablePassthru, StaticLibrary)
+from mod_pbxproj.mod_pbxproj import XcodeProject, PBXFileReference, PBXBuildFile
+
+
+class XcodeBackend(CommonBackend):
+    """Backend that generates XCode project files.
+    """
+
+    def _init(self):
+        self._per_dir_defines_includes_and_flags = {}
+        self._per_dir_sources_and_flags = {}
+        self._topobjdir = None
+        self._topsrcdir = None
+        self._xcode_groups = {}
+
+        CommonBackend._init(self)
+
+        xcode_proj_path = os.path.join(self.environment.topobjdir, 'firefox.xcodeproj/project.pbxproj')
+        try:
+            os.mkdir(os.path.dirname(xcode_proj_path))
+        except OSError:
+            pass
+
+        self._current_path = os.path.dirname(os.path.realpath(__file__))
+
+        shutil.copy(os.path.join(self._current_path, 'templates/xcode/stub_project.pbxproj'),
+                    xcode_proj_path)
+
+        self._xcode_project = XcodeProject.Load(xcode_proj_path)
+
+        PBXFileReference.types['.asm'] = ('sourcecode.nasm', 'PBXSourcesBuildPhase')
+        PBXFileReference.types['.cc'] = ('sourcecode.cpp.cpp', 'PBXSourcesBuildPhase')
+        PBXFileReference.types['.cxx'] = ('sourcecode.cpp.cpp', 'PBXSourcesBuildPhase')
+
+        def detailed(summary):
+            return ('Generates XCode project. Files will compile, predictively show errors, and code-complete.' +
+                    ' Actually building Firefox still requires command-line mach.')
+
+        self.summary.backend_detailed_summary = types.MethodType(detailed, self.summary)
+
+    def consume_object(self, obj):
+        obj.ack()
+
+        # I am treating this path as the current module, I am defining module as having a mozbuild.
+        # Sometimes cpp files are referenced like <module_dir>/file.cpp, other times,
+        # <module_dir>/a/path/to/file.cpp.
+        module_dir = getattr(obj, 'relativedir', None)
+        if not module_dir:
+            return
+
+        if not self._topsrcdir:
+            self._topsrcdir = obj.topsrcdir
+        if not self._topobjdir:
+            self._topobjdir = obj.topobjdir
+
+        if isinstance(obj, DirectoryTraversal):
+            self._add_per_relobjdir_build_args(obj.relobjdir,
+                                        {'-I' + obj.srcdir,
+                                         '-DDEBUG',
+                                         '-DMOZ_APP_VERSION=\\"' + obj.config.substs["MOZ_APP_VERSION"] + '\\"'})
+
+            objinc = self._topobjdir + "/" + module_dir
+            if os.path.exists(objinc):
+                self._add_per_relobjdir_build_args(obj.relobjdir, {'-I' + objinc})
+
+            def parse_prefix_and_defines(flags, current_dir):
+                result = set()
+                flag_list = flags.split(' ')
+                for i in range(0, len(flag_list)):
+                    if flag_list[i][:2] in ('-D', '-U', '-I'):
+                        result.add(flag_list[i])
+                    if flag_list[i].startswith('-include'):
+                        path = flag_list[i + 1].replace('$(DEPTH)', current_dir)
+                        result.add(flag_list[i] + ' ' + path)
+                return result
+
+            build_args = parse_prefix_and_defines(obj.config.substs['OS_COMPILE_CFLAGS'], obj.topobjdir)
+            self._add_per_relobjdir_build_args(obj.relobjdir, build_args)
+
+            # todo: mystery as to why this is missing
+            if 'gfx' in module_dir:
+                self._add_per_relobjdir_build_args(obj.relobjdir, {'-I' + os.path.join(self._topobjdir, 'dist/include/cairo')})
+
+        if isinstance(obj, Exports):
+            for path, files in obj.exports.walk():
+                if not files:
+                    continue
+
+                for f in files:
+                    self._add_per_dir_source_and_flags(module_dir, f, self._topobjdir, obj.relobjdir, True)
+
+        elif isinstance(obj, VariablePassthru):
+
+            def defines_to_string(key, value):
+                def quote_define(val):
+                    val = str(val)
+                    if val.isdigit():
+                        return val
+                    if val is 'True':
+                        return '1'
+                    if val is 'False':
+                        return '0'
+                    return "'" + val + "'"
+
+                return "-D" + str(key) + "=" + quote_define(value)
+
+            added_flags = set()
+            for key, value in obj.variables.items():
+                if isinstance(value, list):
+                    added_flags.update(set(value))
+                else:
+                    added_flags.add(defines_to_string(key, value))
+
+            pattern = re.compile(r'\S+\.js|\S+\.manifest|\S+\.py|\S+\.cpp', re.IGNORECASE)
+            added_flags = {x for x in added_flags if not re.match(pattern, x)}
+            if '-std=c99' in added_flags:
+                added_flags.remove('-std=c99')
+            self._add_per_relobjdir_build_args(obj.relobjdir, added_flags)
+
+        elif isinstance(obj, PerSourceFlag):
+            self._add_per_dir_source_and_flags(module_dir, obj.file_name, self._topsrcdir, obj.relobjdir, True, ' '.join(obj.flags))
+
+        elif isinstance(obj, StaticLibrary):
+            flags = set(obj.defines.get_defines())
+            if not flags:
+                return
+            self._add_per_relobjdir_build_args(obj.relobjdir, flags)
+
+        elif isinstance(obj, Defines):
+            self._add_per_relobjdir_build_args(obj.relobjdir, set(obj.get_defines()))
+
+        elif isinstance(obj, Sources) or isinstance(obj, GeneratedSources) \
+                or isinstance(obj, HostSources) or isinstance(obj, UnifiedSources):
+
+            if isinstance(obj, UnifiedSources):
+                for f in obj.files:
+                    self._add_per_dir_source_and_flags(module_dir, f, self._topsrcdir, obj.relobjdir, is_built=False)
+                for file_map in obj.unified_source_mapping:
+                    unified_file = file_map[0]
+                    self._add_per_dir_source_and_flags(module_dir, unified_file,
+                                                       self._topobjdir, obj.relobjdir, is_built=True)
+
+                # todo looking at the preprocessed output, unistd is present, however,
+                # without this mystery include, compilation fails
+                if 'Unified_cpp_xpcom_build2' in unified_file:
+                    mystery_include = ' -include unistd.h '
+                    self._add_per_dir_source_and_flags(module_dir, unified_file,
+                                                       self._topobjdir, obj.relobjdir, flags=mystery_include)
+            else:
+                for f in obj.files:
+                    self._add_per_dir_source_and_flags(module_dir, f, self._topsrcdir, obj.relobjdir)
+
+        elif isinstance(obj, LocalInclude) or isinstance(obj, GeneratedInclude):
+            topdir = obj.topsrcdir if isinstance(obj, LocalInclude) else obj.topobjdir
+            if obj.path.startswith('/'):
+                path = os.path.join(topdir, obj.path[1:])
+            else:
+                path = os.path.join(topdir, module_dir, obj.path)
+
+            if os.path.exists(path):
+                self._add_per_relobjdir_build_args(obj.relobjdir, {'-I' + path})
+
+    def consume_finished(self):
+        self._flush_to_xcode()
+
+        # For reasons unknown, various includes are missing. Adding them manually.
+        import glob
+        missing_topsrc_includes = glob.glob(os.path.join(self._topsrcdir, 'security/nss/lib') + "/*")
+        # sqlite conflicts, remove it
+        missing_topsrc_includes = [x for x in missing_topsrc_includes if 'sqlite' not in x]
+        missing_topsrc_includes.extend(['netwerk/sctp/src',
+                                        'ipc/chromium/src',
+                                        'xpcom/tests',
+                                        'security/nss/lib/freebl/ecl'])
+        output_xcconfig = os.path.join(self._topobjdir, 'config.xcconfig')
+        shutil.copyfile(os.path.join(self._current_path, 'templates/xcode/stub_xcconfig'), output_xcconfig)
+        f = open(output_xcconfig, 'a')
+        f.write("\nGCC_PREPROCESSOR_DEFINITIONS = NO_NSPR_10_SUPPORT=1 N_UNDF=0x0")
+        f.write("\nHEADER_SEARCH_PATHS = " +
+                ' '.join([os.path.join(self._topsrcdir, x) for x in missing_topsrc_includes]) + ' ' +
+                os.path.join(self._topobjdir, 'dist/include/nss') + ' ' +
+                os.path.join(self._topobjdir, 'dist/include/nspr'))
+        f.close()
+
+        self._xcode_project.save(sort=True)
+
+        print 'Xcode file is at: ' + self._xcode_project.pbxproj_path
+
+    def _add_per_dir_source_and_flags(self, directory, file, abs_src_path, relobjdir, is_built=True, flags=''):
+        # some files can arrive as a full path to file
+        if abs_src_path in file:
+            file = file.replace(abs_src_path + '/', '').replace(directory + '/', '')
+
+        if directory not in self._per_dir_sources_and_flags:
+            self._per_dir_sources_and_flags[directory] = {}
+        if file not in self._per_dir_sources_and_flags[directory]:
+            self._per_dir_sources_and_flags[directory][file] = {'abs_src_path': abs_src_path, 'is_built': is_built,
+                                                                'flags': flags, 'relobjdir': relobjdir}
+
+        if flags:
+            self._per_dir_sources_and_flags[directory][file]['flags'] += ' ' + flags
+
+        if file.endswith('.h'):
+            return
+
+        # look for a matching header file
+        header_file = file.rsplit(".", 1)[0] + ".h"
+        for src_path in (self._topsrcdir, self._topobjdir):
+            if os.path.exists(os.path.join(src_path, directory + '/' + header_file)):
+                self._add_per_dir_source_and_flags(directory, header_file, src_path, relobjdir)
+                break
+
+    def _add_per_relobjdir_build_args(self, directory, set_of_build_args):
+        if directory not in self._per_dir_defines_includes_and_flags:
+            self._per_dir_defines_includes_and_flags[directory] = list(set_of_build_args)
+        else:
+            existing = self._per_dir_defines_includes_and_flags[directory]
+            args = [x for x in set_of_build_args if x not in existing]
+            self._per_dir_defines_includes_and_flags[directory].extend(args)
+
+    def _get_or_create_xcode_group(self, abs_src_path, sub_path, depth=0):
+        if not sub_path or len(sub_path) < 2:
+            return None
+
+        path = os.path.join(abs_src_path, sub_path)
+
+        if path in self._xcode_groups:
+            return self._xcode_groups[path]
+
+        parent_group = None
+        if '/' in sub_path:
+            parent_group = self._get_or_create_xcode_group(abs_src_path, os.path.dirname(sub_path), depth + 1)
+
+        leaf_dir_name = os.path.basename(sub_path)
+        group = self._xcode_project.get_or_create_group(leaf_dir_name, path, parent_group)
+        self._xcode_groups[path] = group
+        return group
+
+    def _add_group_to_xcode(self, abs_path_to_srcs, sub_path, file):
+        if '/' in file:
+            folder = os.path.dirname(file)
+            sub_path = os.path.join(sub_path, folder)
+
+        group = self._get_or_create_xcode_group(abs_path_to_srcs, sub_path)
+        return group
+
+    def _add_file_to_xcode_group(self, group, full_path_to_file, is_built, flags):
+        result = self._xcode_project.add_file(full_path_to_file, group,
+                                              create_build_files=is_built, ignore_unknown_type=True)
+        if len(result) == 0:
+            return
+
+        for item in result:
+            if isinstance(item, PBXBuildFile) and flags:
+                item.add_compiler_flag(flags)
+
+    def _flush_to_xcode(self):
+        for module, files_and_flags in self._per_dir_sources_and_flags.items():
+            for f, flags in files_and_flags.items():
+                group = self._add_group_to_xcode(flags['abs_src_path'], module, f)
+                compiler_flags = flags['flags']
+                if flags['relobjdir'] in self._per_dir_defines_includes_and_flags:
+                    compiler_flags += ' ' + ' '.join(self._per_dir_defines_includes_and_flags[flags['relobjdir']])
+                if compiler_flags:
+                    # add these at end, or files in these paths will conflict
+                    compiler_flags += ' -I{0}/intl/icu/source/common -I{0}/intl/icu/source/i18n'.format(self._topsrcdir) + \
+                                      ' -I' + os.path.join(self._topobjdir, 'dist/include')
+
+                # It seemed xcode treats double spaces between flags as terminators, so  remove those,
+                # and cleanup any includes with trailing slashes, for aesthetics.
+                compiler_flags = compiler_flags.replace('  -', ' -').replace('/ -', ' -')
+                full_path = os.path.join(flags['abs_src_path'], module + '/' + f)
+                self._add_file_to_xcode_group(group, full_path, flags['is_built'], compiler_flags)
\ No newline at end of file

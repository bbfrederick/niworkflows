# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Interfaces for handling BIDS-like neuroimaging structures
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

"""

import os
from pathlib import Path
from shutil import copytree, rmtree

import nibabel as nb

from nipype import logging
from nipype.interfaces.base import (
    traits, isdefined, Undefined,
    TraitedSpec, BaseInterfaceInputSpec, DynamicTraitedSpec,
    File, Directory, InputMultiObject, OutputMultiObject, Str,
    SimpleInterface,
)
from templateflow.api import templates as _get_template_list
from ..utils.bids import BIDS_NAME, _init_layout
from ..utils.misc import splitext as _splitext, _copy_any


STANDARD_SPACES = _get_template_list()
LOGGER = logging.getLogger('nipype.interface')


class BIDSBaseInputSpec(BaseInterfaceInputSpec):
    bids_dir = traits.Either(
        (None, Directory(exists=True)), usedefault=True,
        desc='optional bids directory to initialize a new layout')
    bids_validate = traits.Bool(True, usedefault=True, desc='enable BIDS validator')


class BIDSInfoInputSpec(BIDSBaseInputSpec):
    in_file = File(mandatory=True, desc='input file, part of a BIDS tree')


class BIDSInfoOutputSpec(DynamicTraitedSpec):
    subject = traits.Str()
    session = traits.Str()
    task = traits.Str()
    acq = traits.Str()
    rec = traits.Str()
    run = traits.Str()
    suffix = traits.Str()


class BIDSInfo(SimpleInterface):
    """
    Extract BIDS entities from a BIDS-conforming path

    This interface uses only the basename, not the path, to determine the
    subject, session, task, run, acquisition or reconstruction.

    >>> bids_info = BIDSInfo(bids_dir=str(datadir / 'ds054'), bids_validate=False)
    >>> bids_info.inputs.in_file = '''\
sub-01/func/ses-retest/sub-01_ses-retest_task-covertverbgeneration_bold.nii.gz'''
    >>> res = bids_info.run()
    >>> res.outputs
    <BLANKLINE>
    acq = <undefined>
    rec = <undefined>
    run = <undefined>
    session = retest
    subject = 01
    suffix = bold
    task = covertverbgeneration
    <BLANKLINE>

    >>> bids_info = BIDSInfo(bids_validate=False)
    >>> bids_info.inputs.in_file = str(
    ...     datadir / 'ds114' / 'sub-01' / 'ses-retest' /
    ...     'func' / 'sub-01_ses-retest_task-covertverbgeneration_bold.nii.gz')
    >>> res = bids_info.run()
    >>> res.outputs
    <BLANKLINE>
    acq = <undefined>
    rec = <undefined>
    run = <undefined>
    session = retest
    subject = 01
    suffix = bold
    task = covertverbgeneration
    <BLANKLINE>

    >>> bids_info = BIDSInfo(bids_validate=False)
    >>> bids_info.inputs.in_file = '''\
sub-01/func/ses-retest/sub-01_ses-retest_task-covertverbgeneration_bold.nii.gz'''
    >>> res = bids_info.run()
    >>> res.outputs
    <BLANKLINE>
    acq = <undefined>
    rec = <undefined>
    run = <undefined>
    session = retest
    subject = 01
    suffix = bold
    task = covertverbgeneration
    <BLANKLINE>

    """
    input_spec = BIDSInfoInputSpec
    output_spec = BIDSInfoOutputSpec
    layout = None

    def _run_interface(self, runtime):
        self.layout = self.inputs.bids_dir or self.layout
        self.layout = _init_layout(self.inputs.in_file,
                                   self.layout,
                                   self.inputs.bids_validate)
        params = self.layout.parse_file_entities(self.inputs.in_file,
                                                 domains=['bids'])
        self._results = {key: params.get(key, Undefined)
                         for key in BIDSInfoOutputSpec().get().keys()}
        return runtime


class BIDSDataGrabberInputSpec(BaseInterfaceInputSpec):
    subject_data = traits.Dict(Str, traits.Any)
    subject_id = Str()


class BIDSDataGrabberOutputSpec(TraitedSpec):
    out_dict = traits.Dict(desc='output data structure')
    fmap = OutputMultiObject(desc='output fieldmaps')
    bold = OutputMultiObject(desc='output functional images')
    sbref = OutputMultiObject(desc='output sbrefs')
    t1w = OutputMultiObject(desc='output T1w images')
    roi = OutputMultiObject(desc='output ROI images')
    t2w = OutputMultiObject(desc='output T2w images')
    flair = OutputMultiObject(desc='output FLAIR images')


class BIDSDataGrabber(SimpleInterface):
    """
    Collect files from a BIDS directory structure

    >>> bids_src = BIDSDataGrabber(anat_only=False)
    >>> bids_src.inputs.subject_data = bids_collect_data(
    ...     str(datadir / 'ds114'), '01', bids_validate=False)[0]
    >>> bids_src.inputs.subject_id = '01'
    >>> res = bids_src.run()
    >>> res.outputs.t1w  # doctest: +ELLIPSIS +NORMALIZE_WHITESPACE
    ['.../ds114/sub-01/ses-retest/anat/sub-01_ses-retest_T1w.nii.gz',
     '.../ds114/sub-01/ses-test/anat/sub-01_ses-test_T1w.nii.gz']

    """
    input_spec = BIDSDataGrabberInputSpec
    output_spec = BIDSDataGrabberOutputSpec
    _require_funcs = True

    def __init__(self, *args, **kwargs):
        anat_only = kwargs.pop('anat_only')
        super(BIDSDataGrabber, self).__init__(*args, **kwargs)
        if anat_only is not None:
            self._require_funcs = not anat_only

    def _run_interface(self, runtime):
        bids_dict = self.inputs.subject_data

        self._results['out_dict'] = bids_dict
        self._results.update(bids_dict)

        if not bids_dict['t1w']:
            raise FileNotFoundError('No T1w images found for subject sub-{}'.format(
                self.inputs.subject_id))

        if self._require_funcs and not bids_dict['bold']:
            raise FileNotFoundError('No functional images found for subject sub-{}'.format(
                self.inputs.subject_id))

        for imtype in ['bold', 't2w', 'flair', 'fmap', 'sbref', 'roi']:
            if not bids_dict[imtype]:
                LOGGER.warning('No "%s" images found for sub-%s',
                               imtype, self.inputs.subject_id)

        return runtime


class DerivativesDataSinkInputSpec(BaseInterfaceInputSpec):
    base_directory = traits.Directory(
        desc='Path to the base directory for storing data.')
    in_file = InputMultiObject(File(exists=True), mandatory=True,
                               desc='the object to be saved')
    source_file = File(exists=False, mandatory=True, desc='the input func file')
    space = Str('', usedefault=True, desc='Label for space field')
    desc = Str('', usedefault=True, desc='Label for description field')
    suffix = Str('', usedefault=True, desc='suffix appended to source_file')
    keep_dtype = traits.Bool(False, usedefault=True, desc='keep datatype suffix')
    extra_values = traits.List(Str)
    compress = traits.Bool(desc="force compression (True) or uncompression (False)"
                                " of the output file (default: same as input)")
    check_hdr = traits.Bool(True, usedefault=True, desc='fix headers of NIfTI outputs')


class DerivativesDataSinkOutputSpec(TraitedSpec):
    out_file = OutputMultiObject(File(exists=True, desc='written file path'))
    compression = OutputMultiObject(
        traits.Bool, desc='whether ``in_file`` was compressed/uncompressed '
                          'or `it was copied directly.')
    fixed_hdr = traits.List(traits.Bool, desc='whether derivative header was fixed')


class DerivativesDataSink(SimpleInterface):
    """
    Saves the `in_file` into a BIDS-Derivatives folder provided
    by `base_directory`, given the input reference `source_file`.

    >>> import tempfile
    >>> tmpdir = Path(tempfile.mkdtemp())
    >>> tmpfile = tmpdir / 'a_temp_file.nii.gz'
    >>> tmpfile.open('w').close()  # "touch" the file
    >>> dsink = DerivativesDataSink(base_directory=str(tmpdir), check_hdr=False)
    >>> dsink.inputs.in_file = str(tmpfile)
    >>> dsink.inputs.source_file = bids_collect_data(
    ...     str(datadir / 'ds114'), '01', bids_validate=False)[0]['t1w'][0]
    >>> dsink.inputs.keep_dtype = True
    >>> dsink.inputs.suffix = 'desc-denoised'
    >>> res = dsink.run()
    >>> res.outputs.out_file  # doctest: +ELLIPSIS
    '.../niworkflows/sub-01/ses-retest/anat/sub-01_ses-retest_desc-denoised_T1w.nii.gz'

    >>> bids_dir = tmpdir / 'bidsroot' / 'sub-02' / 'ses-noanat' / 'func'
    >>> bids_dir.mkdir(parents=True, exist_ok=True)
    >>> tricky_source = bids_dir / 'sub-02_ses-noanat_task-rest_run-01_bold.nii.gz'
    >>> tricky_source.open('w').close()
    >>> dsink = DerivativesDataSink(base_directory=str(tmpdir), check_hdr=False)
    >>> dsink.inputs.in_file = str(tmpfile)
    >>> dsink.inputs.source_file = str(tricky_source)
    >>> dsink.inputs.keep_dtype = True
    >>> dsink.inputs.desc = 'preproc'
    >>> res = dsink.run()
    >>> res.outputs.out_file  # doctest: +ELLIPSIS
    '.../niworkflows/sub-02/ses-noanat/func/sub-02_ses-noanat_task-rest_run-01_\
desc-preproc_bold.nii.gz'

    """
    input_spec = DerivativesDataSinkInputSpec
    output_spec = DerivativesDataSinkOutputSpec
    out_path_base = "niworkflows"
    _always_run = True

    def __init__(self, out_path_base=None, **inputs):
        super(DerivativesDataSink, self).__init__(**inputs)
        self._results['out_file'] = []
        if out_path_base:
            self.out_path_base = out_path_base

    def _run_interface(self, runtime):
        src_fname, _ = _splitext(self.inputs.source_file)
        src_fname, dtype = src_fname.rsplit('_', 1)
        _, ext = _splitext(self.inputs.in_file[0])
        if self.inputs.compress is True and not ext.endswith('.gz'):
            ext += '.gz'
        elif self.inputs.compress is False and ext.endswith('.gz'):
            ext = ext[:-3]

        m = BIDS_NAME.search(src_fname)

        mod = Path(self.inputs.source_file).parent.name

        base_directory = runtime.cwd
        if isdefined(self.inputs.base_directory):
            base_directory = self.inputs.base_directory

        base_directory = Path(base_directory).resolve()
        out_path = base_directory / self.out_path_base / \
            '{subject_id}'.format(**m.groupdict())

        if m.groupdict().get('session_id') is not None:
            out_path = out_path / '{session_id}'.format(**m.groupdict())

        out_path = out_path / '{}'.format(mod)
        out_path.mkdir(exist_ok=True, parents=True)
        base_fname = str(out_path / src_fname)

        formatstr = '{bname}{space}{desc}{extra}{suffix}{dtype}{ext}'
        if len(self.inputs.in_file) > 1 and not isdefined(self.inputs.extra_values):
            formatstr = '{bname}{space}{desc}{suffix}{i:04d}{dtype}{ext}'

        space = '_space-{}'.format(self.inputs.space) if self.inputs.space else ''
        desc = '_desc-{}'.format(self.inputs.desc) if self.inputs.desc else ''
        suffix = '_{}'.format(self.inputs.suffix) if self.inputs.suffix else ''
        dtype = '' if not self.inputs.keep_dtype else ('_%s' % dtype)

        self._results['compression'] = []
        self._results['fixed_hdr'] = [False] * len(self.inputs.in_file)
        for i, fname in enumerate(self.inputs.in_file):
            extra = ''
            if isdefined(self.inputs.extra_values):
                extra = '_{}'.format(self.inputs.extra_values[i])
            out_file = formatstr.format(
                bname=base_fname,
                space=space,
                desc=desc,
                extra=extra,
                suffix=suffix,
                i=i,
                dtype=dtype,
                ext=ext,
            )
            self._results['out_file'].append(out_file)
            self._results['compression'].append(_copy_any(fname, out_file))

            is_nii = out_file.endswith('.nii') or out_file.endswith('.nii.gz')
            if self.inputs.check_hdr and is_nii:
                nii = nb.load(out_file)
                hdr = nii.header.copy()
                curr_units = tuple([None if u == 'unknown' else u
                                    for u in hdr.get_xyzt_units()])
                curr_codes = (int(hdr['qform_code']), int(hdr['sform_code']))

                # Default to mm, use sec if data type is bold
                units = (curr_units[0] or 'mm', 'sec' if dtype == '_bold' else None)
                xcodes = (1, 1)  # Derivative in its original scanner space
                if self.inputs.space:
                    xcodes = (4, 4) if self.inputs.space in STANDARD_SPACES \
                        else (2, 2)

                if curr_codes != xcodes or curr_units != units:
                    self._results['fixed_hdr'][i] = True
                    hdr.set_qform(nii.affine, xcodes[0])
                    hdr.set_sform(nii.affine, xcodes[1])
                    hdr.set_xyzt_units(*units)

                    # Rewrite file with new header
                    nii.__class__(nii.get_data(), nii.affine, hdr).to_filename(
                        out_file)

        return runtime


class ReadSidecarJSONInputSpec(BIDSBaseInputSpec):
    in_file = File(exists=True, mandatory=True, desc='the input nifti file')
    fields = InputMultiObject(traits.Str,
                              desc='map these fields to the output object')
    undef_fields = traits.Bool(False, usedefault=True,
                               desc='allow fields to be undefined')


class ReadSidecarJSONOutputSpec(BIDSInfoOutputSpec):
    out_dict = traits.Dict()


class ReadSidecarJSON(SimpleInterface):
    """
    A utility to find and read JSON sidecar files of a BIDS tree

    >>> fmap = str(datadir / 'ds054' / 'sub-100185' / 'fmap' /
    ...            'sub-100185_phasediff.nii.gz')

    >>> meta = ReadSidecarJSON(in_file=fmap, bids_dir=str(datadir / 'ds054'),
    ...                        bids_validate=False).run()
    >>> meta.outputs.subject
    '100185'
    >>> meta.outputs.suffix
    'phasediff'
    >>> meta.outputs.out_dict['Manufacturer']
    'SIEMENS'
    >>> meta = ReadSidecarJSON(in_file=fmap, fields=['Manufacturer'],
    ...                        bids_dir=str(datadir / 'ds054'),
    ...                        bids_validate=False).run()
    >>> meta.outputs.out_dict['Manufacturer']
    'SIEMENS'
    >>> meta.outputs.Manufacturer
    'SIEMENS'
    >>> meta.outputs.OtherField  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
    AttributeError:
    >>> meta = ReadSidecarJSON(
    ...     in_file=fmap, fields=['MadeUpField'],
    ...     bids_dir=str(datadir / 'ds054'),
    ...     bids_validate=False).run()  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
    KeyError:
    >>> meta = ReadSidecarJSON(in_file=fmap, fields=['MadeUpField'],
    ...                        undef_fields=True,
    ...                        bids_dir=str(datadir / 'ds054'),
    ...                        bids_validate=False).run()
    >>> meta.outputs.MadeUpField
    <undefined>

    """
    input_spec = ReadSidecarJSONInputSpec
    output_spec = ReadSidecarJSONOutputSpec
    layout = None
    _always_run = True

    def _run_interface(self, runtime):
        self.layout = self.inputs.bids_dir or self.layout
        self.layout = _init_layout(self.inputs.in_file,
                                   self.layout,
                                   self.inputs.bids_validate)

        # Fill in BIDS entities of the output ("*_id")
        output_keys = [key for key in list(BIDSInfoOutputSpec().get().keys())]
        params = self.layout.parse_file_entities(self.inputs.in_file)
        self._results = {key: params.get(key.split('_')[0], Undefined)
                         for key in output_keys}

        # Fill in metadata
        metadata = self.layout.get_metadata(self.inputs.in_file)
        self._results['out_dict'] = metadata

        # Set dynamic outputs if fields input is present
        if isdefined(self.inputs.fields) and self.inputs.fields:
            for fname in self.inputs.fields:
                if not self.inputs.undef_fields and fname not in metadata:
                    raise KeyError(
                        'Metadata field "%s" not found for file %s' % (
                            fname, self.inputs.in_file))
                self._results[fname] = metadata.get(fname, Undefined)
        return runtime


class BIDSFreeSurferDirInputSpec(BaseInterfaceInputSpec):
    derivatives = Directory(exists=True, mandatory=True,
                            desc='BIDS derivatives directory')
    freesurfer_home = Directory(exists=True, mandatory=True,
                                desc='FreeSurfer installation directory')
    subjects_dir = traits.Str('freesurfer', usedefault=True,
                              desc='Name of FreeSurfer subjects directory')
    spaces = traits.List(traits.Str, desc='Set of output spaces to prepare')
    overwrite_fsaverage = traits.Bool(False, usedefault=True,
                                      desc='Overwrite fsaverage directories, if present')


class BIDSFreeSurferDirOutputSpec(TraitedSpec):
    subjects_dir = traits.Directory(exists=True,
                                    desc='FreeSurfer subjects directory')


class BIDSFreeSurferDir(SimpleInterface):
    """Create a FreeSurfer subjects directory in a BIDS derivatives directory
    and copy fsaverage from the local FreeSurfer distribution.

    Output subjects_dir = ``{derivatives}/{subjects_dir}``, and may be passed to
    ReconAll and other FreeSurfer interfaces.
    """
    input_spec = BIDSFreeSurferDirInputSpec
    output_spec = BIDSFreeSurferDirOutputSpec
    _always_run = True

    def _run_interface(self, runtime):
        subjects_dir = os.path.join(self.inputs.derivatives,
                                    self.inputs.subjects_dir)
        os.makedirs(subjects_dir, exist_ok=True)
        self._results['subjects_dir'] = subjects_dir

        spaces = list(self.inputs.spaces)
        # Always copy fsaverage, for proper recon-all functionality
        if 'fsaverage' not in spaces:
            spaces.append('fsaverage')

        for space in spaces:
            # Skip non-freesurfer spaces and fsnative
            if not space.startswith('fsaverage'):
                continue
            source = os.path.join(self.inputs.freesurfer_home, 'subjects', space)
            dest = os.path.join(subjects_dir, space)
            # Finesse is overrated. Either leave it alone or completely clobber it.
            if os.path.exists(dest) and self.inputs.overwrite_fsaverage:
                rmtree(dest)
            if not os.path.exists(dest):
                try:
                    copytree(source, dest)
                except FileExistsError:
                    LOGGER.warning("%s exists; if multiple jobs are running in parallel"
                                   ", this can be safely ignored", dest)

        return runtime

UPDATE identities      SET id='companion', subject_class='companion' WHERE id='owner';
UPDATE entity_aliases  SET canonical_entity_id='companion' WHERE canonical_entity_id='owner';
UPDATE facts           SET subject_class='companion' WHERE subject_class='owner';
UPDATE preferences     SET subject_class='companion' WHERE subject_class='owner';
UPDATE episodes        SET subject_class='companion' WHERE subject_class='owner';
UPDATE notes           SET subject_class='companion' WHERE subject_class='owner';
UPDATE procedures      SET subject_class='companion' WHERE subject_class='owner';
UPDATE core_memory     SET subject_class='companion' WHERE subject_class='owner';
